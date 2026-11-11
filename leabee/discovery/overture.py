"""Overture Maps places: an open dataset (Overture Maps Foundation, CDLA Permissive 2.0) of tens of millions of businesses with phones,
websites, emails and addresses. Free and key-less: read straight from Overture's public S3 files with PyArrow (anonymous access),
filtering by bounding box first so only a small slice is downloaded.   pip install pyarrow   (listed in requirements-extras.txt)

Attribution: (c) Overture Maps Foundation."""
from __future__ import annotations

import os
import re
import time
from functools import lru_cache

import requests

BUCKET = "overturemaps-us-west-2"
STAC = "https://stac.overturemaps.org/catalog.json"
FALLBACK_RELEASE = "2026-10-21.0"

# our trade label -> substrings looked for in Overture's category taxonomy
TERMS = {
    "plumber": ["plumb"], "electrician": ["electric"], "HVAC contractor": ["hvac", "heating", "air_condition", "air-condition"],
    "roofer": ["roof"], "carpenter": ["carpent"], "painter": ["painter", "painting"], "locksmith": ["locksmith"], "landscaper": ["landscap", "lawn", "garden"],
    "dentist": ["dentist"], "veterinarian": ["veterinar"], "clinic": ["clinic", "doctor", "physician"], "pharmacy": ["pharmac"], "lawyer": ["lawyer", "attorney", "law_firm"],
    "accountant": ["accountant", "bookkeep", "tax_prep"], "real estate agent": ["real_estate_agent", "real_estate_agen", "realtor"], "insurance agency": ["insurance"],
    "restaurant": ["restaurant"], "cafe": ["cafe", "coffee"], "bakery": ["bakery"], "hair salon / barber": ["hair_salon", "barber", "hairdress"], "beauty salon": ["beauty", "nail_salon"],
    "gym": ["gym", "fitness"], "auto repair": ["auto_repair", "automotive_repair", "mechanic"], "car dealer": ["car_dealer"], "photographer": ["photograph"],
    "florist": ["florist"], "hotel": ["hotel", "motel"], "marketing agency": ["marketing", "advertising"], "IT company": ["software_development", "web_design", "it_service"],
    "school": ["school", "tutor"],
}


class OvertureUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=1)
def latest_release() -> str:
    env = os.environ.get("LEABEE_OVERTURE_RELEASE", "").strip()
    if env:
        return env
    try:
        v = requests.get(STAC, timeout=20).json().get("latest")
        if isinstance(v, str) and re.match(r"\d{4}-\d{2}-\d{2}", v):
            return v
    except (requests.RequestException, ValueError):
        pass
    return FALLBACK_RELEASE


def terms_for(label: str, text: str) -> list[str]:
    if label in TERMS:
        return TERMS[label]
    stem = re.sub(r"(?:ers|er|ors|or|ists|ist|s)$", "", re.sub(r"[^a-z ]", "", text.lower()).strip().split(" ")[-1] if text.strip() else "")
    return [stem] if len(stem) >= 4 else []


def _first(v) -> str:
    return (v[0] if isinstance(v, list) and v else v or "") or ""


def search(terms: list[str], bbox: list[float], region: str = "", locality: str = "", limit: int = 300, budget_s: float = 150) -> list[dict]:
    """bbox = [south, north, west, east] (Nominatim order). Returns records shaped like the OpenStreetMap ones."""
    try:
        import pyarrow.compute as pc
        import pyarrow.dataset as ds
        from pyarrow import fs
    except ImportError as e:
        raise OvertureUnavailable("pip install pyarrow") from e
    if not terms or len(bbox) != 4:
        return []
    south, north, west, east = bbox
    rel = latest_release()
    try:
        s3 = fs.S3FileSystem(anonymous=True, region="us-west-2")
        d = ds.dataset(f"{BUCKET}/release/{rel}/theme=places/type=place/", filesystem=s3, format="parquet")
    except Exception as e:      # noqa: BLE001
        raise OvertureUnavailable(f"{e.__class__.__name__}") from e
    cat = pc.field("taxonomy", "primary")
    match = None
    for t in terms:
        m = pc.match_substring(cat, t, ignore_case=True) | pc.match_substring(pc.field("basic_category"), t, ignore_case=True)
        match = m if match is None else (match | m)
    flt = ((pc.field("bbox", "xmin") >= west) & (pc.field("bbox", "xmin") <= east) & (pc.field("bbox", "ymin") >= south) & (pc.field("bbox", "ymin") <= north) & match)
    cols = ["id", "names", "taxonomy", "phones", "websites", "emails", "socials", "addresses", "confidence", "operating_status", "bbox"]
    out, t0 = [], time.time()
    try:
        for batch in d.scanner(columns=cols, filter=flt, batch_size=5000).to_batches():
            for r in batch.to_pylist():
                if str(r.get("operating_status") or "open").lower() in ("closed", "permanently_closed"):
                    continue
                addr = (r.get("addresses") or [{}])[0] or {}
                if region and (addr.get("region") or "").upper() not in (region.upper(), ""):
                    continue
                if locality and (addr.get("locality") or "").lower() != locality.lower():
                    continue
                name = ((r.get("names") or {}).get("primary") or "").strip()
                if not name:
                    continue
                web = _first(r.get("websites"))
                parts = [addr.get("freeform") or "", addr.get("locality") or "", " ".join(x for x in (addr.get("region") or "", addr.get("postcode") or "") if x)]
                bb = r.get("bbox") or {}
                out.append({"name": name, "trade": (r.get("taxonomy") or {}).get("primary", ""), "phone": _first(r.get("phones")), "website": web,
                            "email": _first(r.get("emails")).lower(), "address": ", ".join(p for p in parts if p), "city": addr.get("locality") or "",
                            "state": addr.get("region") or "", "postcode": addr.get("postcode") or "", "hours": "",
                            "socials": {re.sub(r"^https?://(?:www\.)?([a-z]+)\..*$", r"\1", u): u for u in (r.get("socials") or []) if isinstance(u, str)},
                            "source": f"https://overturemaps.org (place {r.get('id')})", "osm_id": f"overture/{r.get('id')}",
                            "lat": bb.get("ymin"), "lon": bb.get("xmin"), "confidence": r.get("confidence")})
            if len(out) >= limit or time.time() - t0 > budget_s:
                break
    except Exception as e:      # noqa: BLE001
        if not out:
            raise OvertureUnavailable(f"{e.__class__.__name__}: {str(e)[:100]}") from e
    return out
