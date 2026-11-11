"""Find real businesses for a prompt like "provide me the details of plumbers in north carolina".

Free and key-less: OpenStreetMap supplies the businesses (name, address, phone, website, email where tagged), then each business's own
website is crawled (plain HTTP, Crawl4AI as fallback) for emails, phones and social links, and every field is checked:
website reachable, email domain exists. Nothing is invented: a missing field stays empty and the answer says how complete the data is.
An LLM (OpenRouter free model or local Ollama) is optional and only used to understand unusual phrasing."""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from .. import llm
from . import osm, overture

_COUNT = re.compile(r"\b(\d{1,2})\b")
_TRAIL = re.compile(r"\b(?:please|thanks|thank you|only|with (?:phone|email|website).*)$", re.I)


def parse_request(text: str, default_n: int = 12) -> dict:
    """"provide me the details of 15 plumbers in north carolina" -> {trade, where, n}. Rules first, LLM only when rules fail."""
    t = text.strip()
    n = default_n
    m = _COUNT.search(t)
    if m and 1 <= int(m.group(1)) <= 50:
        n = int(m.group(1))
    parts = re.split(r"\s+(?:in|near|around|within|across)\s+", t, maxsplit=1, flags=re.I)
    if len(parts) == 2:
        trade = osm.FILLER.sub(" ", parts[0])
        trade = re.sub(r"\d+", " ", trade)
        trade = re.sub(r"\s+", " ", trade).strip(" ,.-")
        where = _TRAIL.sub("", parts[1]).strip(" ,.?!")
        if trade and where:
            return {"trade": trade, "where": where, "n": n}
    d = llm.complete_json(f'Extract the business type and the place from this request. Reply ONLY JSON {{"trade": "...", "where": "...", "n": <number or null>}}.\nRequest: {t}',
                          max_tokens=80, temperature=0) if llm.available() else None
    if isinstance(d, dict) and d.get("trade") and d.get("where"):
        return {"trade": str(d["trade"]), "where": str(d["where"]), "n": int(d["n"]) if str(d.get("n") or "").isdigit() else n}
    return {"trade": "", "where": "", "n": n, "error": 'Say what and where, for example: "plumbers in North Carolina".'}


def completeness(b: dict) -> int:
    return (3 if b.get("phone") else 0) + (3 if b.get("website") else 0) + (3 if b.get("email") else 0) + (1 if b.get("address") else 0) + (1 if b.get("hours") else 0)


def normalize_phone(p: str) -> str:
    """US/Canada numbers to +1 NXX NXX XXXX; anything else is only tidied (never guessed)."""
    p = (p or "").split(";")[0].strip()
    d = re.sub(r"\D", "", p)
    if len(d) == 10 or (len(d) == 11 and d.startswith("1")):
        d = d[-10:]
        return f"+1 {d[:3]} {d[3:6]} {d[6:]}"
    return re.sub(r"\s+", " ", p)


def _norm_site(w: str) -> str:
    w = (w or "").strip()
    return w if not w or w.lower().startswith("http") else "https://" + w


def _dedupe(records: list[dict]) -> list[dict]:
    """Drop duplicates (same name+city, phone or website domain) and merge their fields so each business keeps the best data from every source."""
    owner: dict = {}
    out: list[dict] = []
    for r in sorted(records, key=completeness, reverse=True):
        dom = (urlsplit(r["website"]).hostname or "").removeprefix("www.") if r.get("website") else ""
        digits = re.sub(r"\D", "", r.get("phone", ""))[-10:]
        keys = {("n", re.sub(r"[^a-z0-9]", "", r["name"].lower()), r.get("city", "").lower())}
        if dom:
            keys.add(("d", dom))
        if len(digits) >= 7:
            keys.add(("p", digits))
        hit = next((owner[k] for k in keys if k in owner), None)
        if hit is None:
            out.append(r)
            hit = r
        else:
            for f in ("phone", "website", "email", "address", "hours", "city", "state", "postcode"):
                if not hit.get(f) and r.get(f):
                    hit[f] = r[f]
            for k2, v in (r.get("socials") or {}).items():
                hit.setdefault("socials", {}).setdefault(k2, v)
            hit["sources"] = sorted(set(hit.get("sources", [hit.get("source", "")]) + [r.get("source", "")]))
        for k in keys:
            owner[k] = hit
    return out


def _enrich_one(b: dict, cfg: dict) -> dict:
    """Crawl the business website for missing email/phone/socials and verify what we have."""
    from ..extractors import email_domain_resolves, enrich_site

    b = dict(b)
    b["verified"] = {"website": None, "email_domain": None}
    if b.get("website"):
        info = enrich_site(b["website"], {**cfg, "extractors": {"page_order": ["plain"]}})
        b["verified"]["website"] = bool(info.get("ok"))
        if info.get("ok"):
            if not b.get("email") and info.get("best_email"):
                b["email"], b["email_source"] = info["best_email"], "website"
            if not b.get("phone") and info.get("phones"):
                b["phone"], b["phone_source"] = info["phones"][0], "website"
            for k, v in (info.get("socials") or {}).items():
                b["socials"].setdefault(k, v)
    if b.get("email"):
        b["verified"]["email_domain"] = email_domain_resolves(b["email"])
        b.setdefault("email_source", "open map data")
        if b.get("website"):
            site = (urlsplit(b["website"]).hostname or "").lower().removeprefix("www.")
            dom = b["email"].rpartition("@")[2]
            b["verified"]["email_matches_website"] = bool(site) and (dom == site or dom.endswith("." + site) or site.endswith("." + dom))
    return b


def find_businesses(request: str, n: int | None = None, cfg: dict | None = None, enrich: bool = True, workers: int = 12, pool_factor: int = 3,
                    store=None, cache_days: int = 7) -> dict:
    """With `store`, results are cached for `cache_days` so repeating a search is instant and does not hit the free servers again."""
    cfg = cfg or {}
    p = parse_request(request, n or 12)
    if n:
        p["n"] = n
    if p.get("error"):
        return {"error": p["error"], "businesses": []}
    key = f"disc:{p['trade'].lower()}|{p['where'].lower()}|{p['n']}"
    if store is not None:
        try:
            hit = json.loads(store.get_meta(key) or "null")
        except ValueError:
            hit = None
        if hit and time.time() - hit.get("at", 0) < cache_days * 86400:
            return {**hit["result"], "notes": ["Served from your cache (refreshes weekly)."] + hit["result"].get("notes", [])}
    res = _find(p, cfg, enrich, workers, pool_factor)
    if store is not None and res.get("businesses"):
        store.set_meta(key, json.dumps({"at": time.time(), "result": res}, ensure_ascii=False))
    return res


def _find(p: dict, cfg: dict, enrich: bool, workers: int, pool_factor: int) -> dict:
    trade_label, filters = osm.resolve_trade(p["trade"])
    if not filters:
        return {"error": f"I could not map '{p['trade']}' to a business type. Try a common trade such as plumber, electrician, dentist or restaurant.", "businesses": []}
    geo = osm.geocode(p["where"])
    if not geo:
        return {"error": f"I could not find the place '{p['where']}'.", "businesses": []}
    addr = geo.get("address") or {}
    iso = str(addr.get("ISO3166-2-lvl4", ""))
    region = iso.split("-")[-1] if iso.startswith("US-") or iso.startswith("CA-") else ""
    kind = geo.get("addresstype") or ""
    locality = (addr.get("city") or addr.get("town") or addr.get("village") or addr.get("municipality") or "") if kind in ("city", "town", "village", "municipality", "") and not (kind == "" and not addr.get("city")) else ""
    if kind in ("state", "region", "province"):
        locality = ""
    sources, errors = {}, []

    def run_osm():
        elements = osm.overpass(osm.build_query(filters, geo))
        return [r for r in (osm.element_to_record(el, trade_label) for el in elements) if r]

    def run_overture():
        return overture.search(overture.terms_for(trade_label, p["trade"]), geo["bbox"], region=region if not locality else region, locality=locality,
                               limit=max(p["n"] * 25, 300))
    with ThreadPoolExecutor(max_workers=2) as ex:
        futs = {"OpenStreetMap": ex.submit(run_osm), "Overture Maps": ex.submit(run_overture)}
        records = []
        for name, f in futs.items():
            try:
                got = f.result()
                sources[name] = len(got)
                records += got
            except (RuntimeError, overture.OvertureUnavailable) as e:
                errors.append(f"{name}: {e}")
    if not records:
        return {"error": "The free map services are busy or unreachable right now (" + "; ".join(errors) + "). Try again in a minute.", "businesses": []}
    for r in records:
        r["phone"], r["website"] = normalize_phone(r.get("phone", "")), _norm_site(r.get("website", ""))
    found = len(records)
    records = _dedupe(records)
    pool = sorted(records, key=completeness, reverse=True)[:max(p["n"] * pool_factor, 30)]
    if enrich and pool:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            pool = list(ex.map(lambda b: _enrich_one(b, cfg), pool))
    for b in pool:
        b["completeness"] = completeness(b)
    # bad websites do not count as complete
    for b in pool:
        v = b.get("verified", {})
        if v.get("website") is False or v.get("email_matches_website") is False or v.get("email_domain") is False:
            b["completeness"] -= 1
    top = sorted(pool, key=lambda b: (b["completeness"], bool(b.get("email")), bool(b.get("phone"))), reverse=True)[:p["n"]]
    full = sum(1 for b in top if b.get("phone") and b.get("website") and b.get("email"))
    stats = {"found_on_map": found, "after_dedupe": len(records), "returned": len(top), "with_phone": sum(1 for b in top if b.get("phone")),
             "with_website": sum(1 for b in top if b.get("website")), "with_email": sum(1 for b in top if b.get("email")), "fully_complete": full}
    used = ", ".join(f"{k} ({v})" for k, v in sources.items())
    notes = [f"Sources: {used} and the businesses' own websites. Open data: (c) OpenStreetMap contributors, (c) Overture Maps Foundation. Nothing is estimated."]
    if errors:
        notes.append("Some free sources were unavailable: " + "; ".join(errors))
    if len(top) < p["n"]:
        notes.append(f"Only {len(top)} matching businesses were found in {geo['name'].split(',')[0]}; coverage of open map data varies by area.")
    if stats["returned"] and stats["with_email"] <= stats["returned"] / 2:
        notes.append("Many small businesses publish no email; contact them by phone or their website form.")
    return {"query": {"trade": trade_label, "where": geo["name"], "n": p["n"]}, "businesses": top, "stats": stats, "notes": notes}


def save_to_pipeline(store, cfg: dict, businesses: list[dict]) -> dict:
    """Store the businesses as Google-Maps-style prospects (scored by need and reach) so you can work them in the dashboard."""
    from ..pipeline import item_to_lead
    from ..prospects import record_to_item

    new = kept = 0
    for b in businesses:
        rec = {"name": b["name"], "main_category": b.get("trade", ""), "website": b.get("website", ""), "phone": b.get("phone", ""),
               "emails": [b["email"]] if b.get("email") else [], "address": b.get("address", ""), "country": "", "link": b["source"],
               "kgmid": b.get("osm_id", ""), **{k: v for k, v in (b.get("socials") or {}).items()}}
        item = record_to_item(rec, source_label="OpenStreetMap")
        lead = item_to_lead(item, {**cfg, "min_score": 0}) if item else None
        if lead:
            lead["details"] = json.dumps({**json.loads(lead["details"]), "verified": b.get("verified", {}), "hours": b.get("hours", "")}, ensure_ascii=False)
            kept += 1
            new += store.upsert(lead)
    return {"saved": kept, "new": new}
