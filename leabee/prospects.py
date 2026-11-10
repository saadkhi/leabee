"""Outbound prospects (e.g. from Google Maps): businesses that may need your services but have not asked yet.

Scored on the same 0-100 scale and grades as client gigs, with different, equally explicit parts:
    Need   0-35   gaps you can fix: no website, weak reviews/rating, unclaimed listing, no way to book/contact online
    Fit    0-25   category is a good buyer of your services (restaurants, clinics, real estate, ...) or is on your target list
    Value  0-15   size proxy: number of reviews (established businesses can pay)
    Reach  0-15   email > mobile phone > landline > website contact form
    Signal 0-10   listing is active and complete (hours, photos, claimed, recent reviews)
"""
from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path

from .models import RawItem
from .scoring import grade

# categories whose owners typically buy websites, local SEO, automation, booking, content
GOOD_BUYERS = re.compile(
    r"restaurant|cafe|coffee|bakery|bar\b|hotel|resort|salon|spa|barber|beauty|gym|fitness|yoga|dentist|dental|clinic|doctor|chiropract|physio|"
    r"veterinar|real estate|realtor|property|law|attorney|lawyer|accountan|consult|agency|contractor|plumb|electric|hvac|roof|cleaning|landscap|"
    r"auto (?:repair|body|detail)|mechanic|car (?:dealer|wash)|school|academy|tutor|photograph|wedding|event|florist|boutique|store|shop|retail|"
    r"travel|tour|moving|storage|pet|daycare|physiotherap|optician|pharmacy|bookkeep|insurance|mortgage|interior|architect|builder", re.I)
CHAIN_HINT = re.compile(r"\b(?:mcdonald|starbucks|subway|walmart|kfc|burger king|domino|pizza hut|7-eleven|costco|ikea|h&m|zara)\b", re.I)


def _f(rec: dict, *names, default=""):
    """Case/space-insensitive field lookup (the desktop app's CSV and API use slightly different key styles)."""
    low = {re.sub(r"[\s\-]+", "_", str(k)).lower(): v for k, v in rec.items()}
    for n in names:
        v = low.get(n)
        if v not in (None, "", [], {}):
            return v
    return default


def _num(v, default=0.0) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _listify(v) -> list[str]:
    if isinstance(v, list):
        return [str(x) for x in v if x]
    if isinstance(v, str) and v:
        return [p.strip() for p in re.split(r"[,;|\n]", v) if p.strip()]
    return []


def normalise(rec: dict) -> dict:
    """Map a Google Maps Extractor record (API result row or CSV row) to the fields Leabee uses."""
    addr = _f(rec, "address", "detailed_address", "full_address")
    detailed = _f(rec, "detailed_address")
    country = ""
    if isinstance(detailed, dict):
        country = detailed.get("country") or detailed.get("country_code") or ""
        addr = addr if isinstance(addr, str) and addr else ", ".join(str(detailed.get(k, "")) for k in ("street", "city", "state", "country") if detailed.get(k))
    status = str(_f(rec, "business_status", "status", default="")).lower()
    socials = {k: _f(rec, k) for k in ("linkedin", "twitter", "facebook", "instagram", "youtube", "github", "tiktok") if _f(rec, k)}
    return {
        "name": str(_f(rec, "name", "title")).strip(),
        "category": str(_f(rec, "main_category", "category", "type")).strip(),
        "categories": _listify(_f(rec, "categories")),
        "website": str(_f(rec, "website", "domain", "url_website")).strip(),
        "phone": str(_f(rec, "phone", "phone_international", "phone_number")).strip(),
        "emails": _listify(_f(rec, "emails", "email", "best_email", "email_1")),
        "address": addr if isinstance(addr, str) else "",
        "country": str(country or _f(rec, "country", "country_code")),
        "rating": _num(_f(rec, "rating", "stars", default=0)),
        "reviews": int(_num(_f(rec, "reviews", "review_count", "reviews_count", default=0))),
        "link": str(_f(rec, "link", "google_maps_url", "maps_url")).strip(),
        "id": str(_f(rec, "kgmid", "place_id", "cid", "data_id", default="")),
        "can_claim": str(_f(rec, "can_claim", "is_claimable", default="")).lower() in ("true", "1", "yes"),
        "closed": any(w in status for w in ("closed", "permanently")),
        "has_hours": bool(_f(rec, "hours", "working_hours", "workday_timing", default="")),
        "description": str(_f(rec, "description", "about", default="")).strip(),
        "socials": socials,
        "owner": str(_f(rec, "owner", "owner_name", "owner_or_contact_name", default="")).strip() if not isinstance(_f(rec, "owner"), dict)
        else str((_f(rec, "owner") or {}).get("name", "")),
    }


def record_to_item(rec: dict, source_label: str = "Google Maps") -> RawItem | None:
    b = normalise(rec)
    if not b["name"] or b["closed"]:
        return None
    where = b["address"].split(",")[-2:] if b["address"] else []
    title = f"{b['name']} ({b['category'] or 'business'})" + (f" · {', '.join(x.strip() for x in where)}" if where else "")
    lines = [f"{b['name']} is a {b['category'] or 'local business'}" + (f" at {b['address']}" if b["address"] else "") + "."]
    if b["description"]:
        lines.append(b["description"][:400])
    lines.append(f"Rating {b['rating']:.1f} from {b['reviews']} reviews." if b["reviews"] else "No reviews yet.")
    lines.append("Website: " + (b["website"] or "none listed") + ".")
    if b["phone"]:
        lines.append(f"Phone: {b['phone']}.")
    if b["emails"]:
        lines.append("Email: " + ", ".join(b["emails"][:3]) + ".")
    for k, v in b["socials"].items():
        lines.append(f"{k}: {v}")
    hint = " ".join(b["emails"][:2] + ([b["phone"]] if b["phone"] else []) + ([b["website"]] if b["website"] else []))
    item = RawItem(source="gmaps", platform=source_label, lead_type="prospect", title=title, text="\n".join(lines),
                   url=b["link"] or (f"https://www.google.com/maps/search/{b['name'].replace(' ', '+')}" if not b["id"] else f"https://www.google.com/maps/?cid={b['id']}"),
                   author=b["name"], author_url=b["website"], posted_at=None, location_hint=b["country"] or b["address"], contact_hint=hint)
    item.meta = b
    return item


def score_prospect(item: RawItem, cfg: dict) -> dict | None:
    from .classify import detect_country, detect_language
    b = getattr(item, "meta", None) or {}
    if not b:
        return None
    gcfg = cfg.get("gmaps") or {}
    prof = cfg.get("profile") or {}
    cat_blob = f"{b.get('category', '')} {' '.join(b.get('categories', []))}"
    if CHAIN_HINT.search(b.get("name", "")):
        return None                      # big chains buy from head office, not from you
    comp, why = {}, []

    # ---- Need 0-35
    need, gaps = 0, []
    if not b["website"]:
        need += 30
        gaps.append("no website")
    else:
        need += 8
        if not b["emails"] and not b["socials"]:
            need += 6
            gaps.append("site with no email or social links")
    if b["reviews"] < 15:
        need += 8
        gaps.append(f"only {b['reviews']} reviews")
    if 0 < b["rating"] < 4.0:
        need += 7
        gaps.append(f"rating {b['rating']:.1f}")
    if b["can_claim"]:
        need += 5
        gaps.append("unclaimed listing")
    comp["need"] = min(35, need)
    why.append(f"+{comp['need']} need: " + (", ".join(gaps) if gaps else "established online presence"))

    # ---- Fit 0-25
    targets = [t.lower() for t in (gcfg.get("target_categories") or [])]
    fit, flabel = (20, "on your target list") if targets and any(t in cat_blob.lower() for t in targets) else \
        (16, "category usually buys web/marketing/automation") if GOOD_BUYERS.search(cat_blob) else (8, "general local business")
    if prof.get("countries_preferred") and (b["country"] in prof["countries_preferred"]):
        fit += 5
        flabel += ", preferred country"
    comp["fit"] = min(25, fit)
    why.append(f"+{comp['fit']} fit: {flabel}")

    # ---- Value 0-15 (size proxy)
    rv = b["reviews"]
    comp["value"] = 15 if rv >= 200 else 11 if rv >= 50 else 7 if rv >= 10 else 3
    why.append(f"+{comp['value']} value: {rv} reviews (size proxy)")

    # ---- Reach 0-15
    phone_mobile = bool(re.match(r"\+?\d[\d\s\-()]{7,}", b["phone"] or ""))
    if b["emails"]:
        reach, rl = 15, "email on record"
    elif b["phone"]:
        reach, rl = 10, "phone number"
    elif b["website"]:
        reach, rl = 6, "website contact form"
    else:
        reach, rl = 2, "no contact details yet (run site enrichment)"
    comp["reach"] = reach
    why.append(f"+{reach} reach: {rl}")

    # ---- Signal 0-10
    sig = 4 + (3 if b["has_hours"] else 0) + (3 if b["can_claim"] else 0)
    comp["signal"] = min(10, sig)
    why.append(f"+{comp['signal']} signal: listing details")

    score = int(sum(comp.values()))
    pitch = ("a website" if not b["website"] else "local SEO and review growth" if (b["rating"] and b["rating"] < 4.2) or rv < 15
             else "workflow automation and online booking")
    contact_bits = b["emails"][:1] + ([b["phone"]] if b["phone"] else [])
    return {
        "language": detect_language(item.text), "categories": _prospect_cats(b, gcfg),
        "score": score, "kind": "prospect", "grade": grade(score), "components": comp,
        "next_action": f"Call or email the owner and pitch {pitch}" + (f" ({gaps[0]})" if gaps else ""),
        "budget_text": "", "budget_usd": None, "contact": " | ".join(contact_bits + ([b["website"]] if b["website"] else [])),
        "country": detect_country(item.text, item.location_hint), "age_hours": None, "reasons": why,
        "details": {"company": b["name"], "website": b["website"], "category": b["category"], "rating": b["rating"], "reviews": b["reviews"],
                    "address": b["address"], "phone": b["phone"], "socials": b["socials"], "pitch": pitch, "gaps": gaps,
                    **({"owner": b["owner"]} if b["owner"] else {})},
        "posted_at": None,
    }


def _prospect_cats(b: dict, gcfg: dict) -> list[str]:
    out = []
    if not b["website"]:
        out += ["web_development", "marketing_seo"]
    else:
        out += ["marketing_seo", "automation"]
    return out


# ---------------------------------------------------------------------------- reading exports
def read_export(path: str | Path) -> list[dict]:
    """Read a Google Maps Extractor export (.csv or .json/.jsonl)."""
    p = Path(path)
    raw = p.read_text(encoding="utf-8-sig", errors="replace")
    if p.suffix.lower() == ".jsonl":
        return [json.loads(l) for l in raw.splitlines() if l.strip()]
    if p.suffix.lower() == ".json":
        d = json.loads(raw)
        return d if isinstance(d, list) else d.get("results") or d.get("data") or []
    return list(csv.DictReader(io.StringIO(raw)))
