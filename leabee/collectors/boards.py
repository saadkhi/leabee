"""Remote job boards with free public APIs/feeds: RemoteOK, Remotive, We Work Remotely, Himalayas."""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from typing import Iterator

from ..models import RawItem
from ..textutil import clip, parse_dt, strip_html

log = logging.getLogger("leabee.boards")

WWR_FEEDS = [
    "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-front-end-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-design-jobs.rss",
    "https://weworkremotely.com/categories/remote-copywriting-jobs.rss",
    "https://weworkremotely.com/categories/remote-marketing-jobs.rss",
    "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
    "https://weworkremotely.com/categories/remote-customer-support-jobs.rss",
    "https://weworkremotely.com/categories/remote-business-exec-management-jobs.rss",
    "https://weworkremotely.com/categories/remote-product-jobs.rss",
    "https://weworkremotely.com/remote-jobs.rss",
]
REMOTIVE_CATEGORIES = ["software-dev", "marketing", "writing", "design", "data", "devops", "customer-support",
                       "product", "qa", "sales", "all-others"]


def parse_remoteok(data: list) -> list[RawItem]:
    out = []
    for j in data:
        if not isinstance(j, dict) or not j.get("position"):
            continue
        desc = strip_html(j.get("description") or "")
        sal = ""
        if j.get("salary_min") and j.get("salary_max"):
            sal = f"\nSalary: ${j['salary_min']}-${j['salary_max']}"
        out.append(RawItem(
            source="remoteok", platform="RemoteOK", lead_type="job",
            title=f"{j['position']} at {j.get('company', '')}".strip(), text=clip(desc, 3500) + sal,
            url=j.get("url") or j.get("apply_url") or "", author=j.get("company") or "",
            posted_at=parse_dt(j.get("epoch") or j.get("date")), location_hint=j.get("location") or "",
            extra_tags=list(j.get("tags") or []), contact_hint=j.get("apply_url") or "",
        ))
    return [i for i in out if i.url]


def parse_remotive(data: dict) -> list[RawItem]:
    out = []
    for j in data.get("jobs", []):
        desc = strip_html(j.get("description") or "")
        sal = f"\nSalary: {j['salary']}" if j.get("salary") else ""
        out.append(RawItem(
            source="remotive", platform="Remotive", lead_type="job",
            title=f"{j.get('title', '')} at {j.get('company_name', '')}", text=clip(desc, 3500) + sal,
            url=j.get("url", ""), author=j.get("company_name") or "", posted_at=parse_dt(j.get("publication_date")),
            location_hint=j.get("candidate_required_location") or "",
            extra_tags=[j.get("category", "")] + list(j.get("tags") or []) + [j.get("job_type", "")],
        ))
    return [i for i in out if i.url]


def parse_wwr(xml_text: str) -> list[RawItem]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or it.findtext("guid") or "").strip()
        if not title or not link:
            continue
        desc = strip_html(it.findtext("description") or "")
        region = (it.findtext("region") or "") + " " + (it.findtext("country") or "")
        tags = [it.findtext("category") or "", it.findtext("type") or "", it.findtext("skills") or ""]
        pub = it.findtext("pubDate")
        dt = None
        if pub:
            from email.utils import parsedate_to_datetime
            try:
                dt = parsedate_to_datetime(pub)
            except (TypeError, ValueError):
                dt = None
        company = title.split(":", 1)[0].strip() if ":" in title else ""
        out.append(RawItem(source="weworkremotely", platform="We Work Remotely", lead_type="job", title=title,
                           text=clip(desc, 3500), url=link, author=company, posted_at=dt,
                           location_hint=region.strip(), extra_tags=[t for t in tags if t]))
    return out


def parse_himalayas(data: dict) -> list[RawItem]:
    out = []
    for j in data.get("jobs", []):
        locs = ", ".join(j.get("locationRestrictions") or [])
        sal = ""
        if j.get("minSalary"):
            sal = f"\nSalary: {j.get('currency', '$')}{j['minSalary']}-{j.get('maxSalary', '')} {j.get('salaryPeriod', '')}"
        out.append(RawItem(
            source="himalayas", platform="Himalayas", lead_type="job",
            title=f"{j.get('title', '')} at {j.get('companyName', '')}",
            text=clip(strip_html(j.get("description") or j.get("excerpt") or ""), 3500) + sal,
            url=j.get("guid") or j.get("applicationLink") or "", author=j.get("companyName") or "",
            posted_at=parse_dt(j.get("pubDate")), location_hint=locs or "Worldwide",
            extra_tags=list(j.get("categories") or []) + [j.get("employmentType") or ""],
            contact_hint=j.get("applicationLink") or "",
        ))
    return [i for i in out if i.url]


def _json(http, url, **kw):
    r = http.get(url, **kw)
    if r is None or r.status_code != 200:
        log.warning("%s -> %s", url, getattr(r, "status_code", "no response"))
        return None
    try:
        return r.json()
    except ValueError:
        return None


def collect(cfg: dict, http) -> Iterator[RawItem]:
    b = cfg.get("boards", {})
    if b.get("remoteok", True):
        data = _json(http, "https://remoteok.com/api", timeout=60)
        if isinstance(data, list):
            yield from parse_remoteok(data)
    if b.get("remotive", True):
        for cat in REMOTIVE_CATEGORIES:
            data = _json(http, "https://remotive.com/api/remote-jobs", params={"category": cat, "limit": 100})
            if isinstance(data, dict):
                yield from parse_remotive(data)
    if b.get("weworkremotely", True):
        seen = set()
        for feed in WWR_FEEDS:
            r = http.get(feed, timeout=60)
            if r is None or r.status_code != 200:
                continue
            for it in parse_wwr(r.text):
                if it.url not in seen:
                    seen.add(it.url)
                    yield it
    if b.get("himalayas", True):
        pages = int(b.get("himalayas_pages", 5))
        for p in range(pages):
            data = _json(http, "https://himalayas.app/jobs/api", params={"limit": 100, "offset": p * 100})
            if not isinstance(data, dict) or not data.get("jobs"):
                break
            yield from parse_himalayas(data)
