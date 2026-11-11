"""Public tenders: UK Contracts Finder (free, no key, OCDS JSON). Governments buy a lot of remote-doable work:
software, websites, data, content, design, marketing. Only these CPV families are kept (72 IT services, 48 software,
79 business/marketing/translation, 73 R&D, 92 media); everything else (taxis, construction...) is skipped."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Iterator

from ..models import RawItem
from ..textutil import parse_dt, strip_html

log = logging.getLogger("leabee.tenders")
API = "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search"
KEEP_CPV = ("72", "48", "79", "73", "92")


def parse_release(rel: dict) -> RawItem | None:
    t = rel.get("tender") or {}
    cpv = str((t.get("classification") or {}).get("id", ""))
    extra = [str(c.get("id", "")) for c in (t.get("additionalClassifications") or []) if isinstance(c, dict)]
    if not any(c.startswith(KEEP_CPV) for c in [cpv, *extra] if c):
        return None
    if str(t.get("status", "active")).lower() not in ("active", "planned"):
        return None
    title = (t.get("title") or "").strip()
    nid = t.get("id") or rel.get("id", "")
    if not title or not nid:
        return None
    buyer = (rel.get("buyer") or {}).get("name", "")
    val = t.get("value") or {}
    end = (t.get("tenderPeriod") or {}).get("endDate", "")
    sme = (t.get("suitability") or {}).get("sme")
    lines = [f"{buyer} is tendering: {title}.", strip_html(t.get("description") or "")[:1500]]
    if val.get("amount"):
        lines.append(f"Budget: £{float(val['amount']):,.0f} (tender value).")
    if end:
        lines.append(f"Deadline: {end[:10]}.")
    if sme:
        lines.append("Suitable for SMEs.")
    lines.append("Paid contract to hire a supplier.")
    return RawItem(source="tenders", platform="UK Contracts Finder", lead_type="buyer", title=f"{title} ({buyer})"[:300], text="\n".join(l for l in lines if l),
                   url=f"https://www.contractsfinder.service.gov.uk/Notice/{str(nid).lower()}", author=buyer, posted_at=parse_dt(t.get("datePublished") or rel.get("date")),
                   force_intent="buyer", location_hint="United Kingdom")


def collect(cfg: dict, http) -> Iterator[RawItem]:
    tcfg = cfg.get("tenders") or {}
    if tcfg.get("enabled") is False:
        return
    days = int(tcfg.get("days", 10))
    now = datetime.now(timezone.utc)
    url = (f"{API}?stages=tender&size=100&publishedFrom={(now - timedelta(days=days)).date()}&publishedTo={now.date()}")
    r = http.get(url, headers={"Accept": "application/json"})
    if r is None or r.status_code != 200:
        log.warning("contracts finder -> %s", getattr(r, "status_code", "no response"))
        return
    try:
        releases = r.json().get("releases", [])
    except ValueError:
        return
    for rel in releases:
        item = parse_release(rel)
        if item:
            yield item
