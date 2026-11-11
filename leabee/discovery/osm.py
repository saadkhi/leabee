"""Free business discovery on OpenStreetMap: Nominatim (place lookup) + Overpass (tagged businesses). No API key, open data (ODbL).

Please keep usage modest: these are community-run servers (Nominatim: max 1 request/second; Overpass: fair use). Results are
cached by the caller. Attribution: (c) OpenStreetMap contributors."""
from __future__ import annotations

import os
import re
import time
from functools import lru_cache

import requests

NOMINATIM = "https://nominatim.openstreetmap.org/search"
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]

# trade keyword (regex) -> (label, OSM tag filters). A name-regex fallback is added for trades without a precise tag.
TRADES: list[tuple[str, str, list[str]]] = [
    (r"plumb", "plumber", ['["craft"="plumber"]']),
    (r"electric", "electrician", ['["craft"="electrician"]']),
    (r"hvac|heating|air.?condition|\bac\b", "HVAC contractor", ['["craft"="hvac"]']),
    (r"roof", "roofer", ['["craft"="roofer"]']),
    (r"carpent|woodwork", "carpenter", ['["craft"="carpenter"]']),
    (r"\bpaint", "painter", ['["craft"="painter"]']),
    (r"locksmith", "locksmith", ['["craft"="locksmith"]', '["shop"="locksmith"]']),
    (r"landscap|gardener|lawn", "landscaper", ['["craft"="gardener"]']),
    (r"dentist|dental", "dentist", ['["amenity"="dentist"]']),
    (r"veterinar|\bvet\b", "veterinarian", ['["amenity"="veterinary"]']),
    (r"doctor|physician|clinic", "clinic", ['["amenity"="clinic"]', '["amenity"="doctors"]']),
    (r"pharmac", "pharmacy", ['["amenity"="pharmacy"]']),
    (r"lawyer|attorney|law firm", "lawyer", ['["office"="lawyer"]']),
    (r"account|bookkeep|\bcpa\b", "accountant", ['["office"="accountant"]']),
    (r"real.?estate|realtor|estate agen", "real estate agent", ['["office"="estate_agent"]']),
    (r"insurance", "insurance agency", ['["office"="insurance"]']),
    (r"restaurant", "restaurant", ['["amenity"="restaurant"]']),
    (r"cafe|coffee", "cafe", ['["amenity"="cafe"]']),
    (r"bakery|bakeries", "bakery", ['["shop"="bakery"]']),
    (r"barber|hair|salon", "hair salon / barber", ['["shop"="hairdresser"]']),
    (r"beauty|spa\b|nail", "beauty salon", ['["shop"="beauty"]']),
    (r"gym|fitness", "gym", ['["leisure"="fitness_centre"]']),
    (r"mechanic|auto.?repair|car repair|garage", "auto repair", ['["shop"="car_repair"]']),
    (r"car dealer|dealership", "car dealer", ['["shop"="car"]']),
    (r"photograph", "photographer", ['["craft"="photographer"]', '["shop"="photo"]']),
    (r"florist", "florist", ['["shop"="florist"]']),
    (r"hotel|motel", "hotel", ['["tourism"="hotel"]', '["tourism"="motel"]']),
    (r"marketing|advertis", "marketing agency", ['["office"="advertising_agency"]']),
    (r"web.?(?:design|develop)|software|it (?:company|services)", "IT company", ['["office"="it"]', '["office"="company"]["name"~"software|web|tech",i]']),
    (r"school|academy|tutor", "school", ['["amenity"="school"]', '["amenity"="language_school"]']),
]
FILLER = re.compile(r"\b(?:provide|give|show|list|find|get|fetch|send|me|us|the|a|an|some|all|top|best|good|leads?|details?|information|info|contacts?|"
                    r"companies|company|businesses|business|of|for|about|on|please|can you|could you|i (?:want|need)|with|complete|real|numbers?|emails?|"
                    r"phone|phones|websites?|addresses|address|and)\b", re.I)


def _ua() -> dict:
    contact = os.environ.get("LEABEE_CONTACT", "").strip()
    return {"User-Agent": f"leabee/1.0 (business lead research{'; ' + contact if contact else ''})"}


def resolve_trade(text: str) -> tuple[str, list[str]]:
    t = text.lower()
    for rx, label, filters in TRADES:
        if re.search(rx, t):
            return label, filters
    stem = re.sub(r"(?:ers|er|ors|or|ists|ist|s)$", "", re.sub(r"[^a-z ]", "", t).strip().split(" ")[-1] if t.strip() else "")
    if len(stem) < 4:
        return text.strip(), []
    return text.strip(), [f'["name"~"{stem}",i]']


@lru_cache(maxsize=64)
def geocode(place: str) -> dict | None:
    r = requests.get(NOMINATIM, params={"q": place, "format": "jsonv2", "limit": 1, "addressdetails": 1}, headers=_ua(), timeout=30)
    if r.status_code != 200 or not r.json():
        return None
    g = r.json()[0]
    return {"name": g.get("display_name", place), "type": g.get("osm_type"), "id": int(g.get("osm_id", 0)),
            "bbox": [float(x) for x in g.get("boundingbox", [])], "address": g.get("address", {}), "addresstype": g.get("addresstype", "")}


def build_query(filters: list[str], geo: dict, limit: int = 400, timeout: int = 90) -> str:
    if geo["type"] == "relation":
        head, scope = f"area(id:{3600000000 + geo['id']})->.a;", "(area.a)"
    elif geo["type"] == "way":
        head, scope = f"area(id:{2400000000 + geo['id']})->.a;", "(area.a)"
    else:
        s, n, w, e = geo["bbox"]
        head, scope = "", f"({s},{w},{n},{e})"
    body = "".join(f"nwr{f}{scope};" for f in filters)
    return f"[out:json][timeout:{timeout}];{head}({body});out center tags {limit};"


def overpass(query: str, timeout: int = 120) -> list[dict]:
    last = ""
    for url in OVERPASS:
        for attempt in range(2):
            try:
                r = requests.post(url, data={"data": query}, headers=_ua(), timeout=timeout)
            except requests.RequestException as e:
                last = e.__class__.__name__
                break
            if r.status_code == 200:
                try:
                    return r.json().get("elements", [])
                except ValueError:
                    last = "bad json"
                    break
            last = f"HTTP {r.status_code}"
            if r.status_code in (429, 504):
                time.sleep(4 * (attempt + 1))
                continue
            break
    raise RuntimeError(f"Overpass unavailable ({last})")


def _first(tags: dict, *keys: str) -> str:
    for k in keys:
        v = tags.get(k)
        if v:
            return str(v).strip()
    return ""


def element_to_record(el: dict, trade: str) -> dict | None:
    t = el.get("tags") or {}
    name = _first(t, "name", "brand", "operator")
    if not name:
        return None
    street = " ".join(x for x in (_first(t, "addr:housenumber"), _first(t, "addr:street")) if x)
    city, state, zipc = _first(t, "addr:city"), _first(t, "addr:state"), _first(t, "addr:postcode")
    address = ", ".join(x for x in (street, city, " ".join(x for x in (state, zipc) if x)) if x)
    socials = {k: _first(t, f"contact:{k}") for k in ("facebook", "instagram", "twitter", "youtube", "linkedin") if _first(t, f"contact:{k}")}
    website = _first(t, "website", "contact:website", "url")
    if website and not website.lower().startswith("http"):
        website = "https://" + website
    kind, oid = el.get("type", "node"), el.get("id")
    c = el.get("center") or {}
    return {"name": name, "trade": trade, "phone": _first(t, "phone", "contact:phone", "contact:mobile", "mobile"), "website": website,
            "email": _first(t, "email", "contact:email").lower(), "address": address, "city": city, "state": state, "postcode": zipc,
            "hours": _first(t, "opening_hours"), "socials": socials, "source": f"https://www.openstreetmap.org/{kind}/{oid}",
            "osm_id": f"{kind}/{oid}", "lat": el.get("lat") or c.get("lat"), "lon": el.get("lon") or c.get("lon")}
