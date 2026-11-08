"""TinyFish Search (worldwide intent sweeps) and Fetch (lead enrichment).

Docs: https://docs.tinyfish.ai/search-api.md  and  /fetch-api.md
Search: GET https://api.search.tinyfish.ai?query=...   (free up to 12,000 req/day)
Fetch : POST https://api.fetch.tinyfish.ai {"urls": [...]}  (free up to 1,000 URLs/day, max 10/request)
"""
from __future__ import annotations

import logging
import os
import random
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Iterator
from urllib.parse import urlsplit

from ..http import Http
from ..models import RawItem

log = logging.getLogger("leabee.tinyfish")
SEARCH_URL = "https://api.search.tinyfish.ai"
FETCH_URL = "https://api.fetch.tinyfish.ai"

PLATFORM_NAMES = {
    "reddit.com": "Reddit", "facebook.com": "Facebook", "x.com": "X (Twitter)", "twitter.com": "X (Twitter)",
    "linkedin.com": "LinkedIn", "quora.com": "Quora", "threads.net": "Threads", "upwork.com": "Upwork",
    "freelancer.com": "Freelancer.com", "peopleperhour.com": "PeoplePerHour", "guru.com": "Guru",
    "indiehackers.com": "Indie Hackers", "news.ycombinator.com": "Hacker News", "producthunt.com": "Product Hunt",
    "dev.to": "DEV", "medium.com": "Medium", "fiverr.com": "Fiverr",
}
# URLs that are people/agencies advertising themselves, not buyers.
SELLER_URL = re.compile(
    r"upwork\.com/(freelancers|agencies|services|hire|resources|ab/)|fiverr\.com|freelancer\.com/(u|hire|freelancers)/|"
    r"linkedin\.com/(in|company|pulse|school|learning)/|peopleperhour\.com/(freelance|hire|services)|guru\.com/(freelancers|d/)|"
    r"facebook\.com/(pages|public|people)/|medium\.com/@?[^/]+/[^/]*(how-to|guide|top-|best-)|"
    r"/(tag|tags|topic|topics|category|categories|hashtag|explore|search)/|reddit\.com/(r/[^/]+/?$|user/|search)|"
    r"news\.ycombinator\.com/(jobs|newest|front|ask|show|news|user|from|submitted)(\?|/|$)",
    re.I)


class TinyFishError(RuntimeError):
    pass


class TinyFishAuthError(TinyFishError):
    pass


def host_platform(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    for dom, name in PLATFORM_NAMES.items():
        if host == dom or host.endswith("." + dom):
            return name
    return host or "Web"


class TinyFishClient:
    def __init__(self, api_key: str | None = None, delay: float = 0.6):
        self.api_key = api_key or os.environ.get("TINYFISH_API_KEY", "")
        self.http = Http(delay=delay, timeout=60)

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict:
        return {"X-API-Key": self.api_key}

    def _check(self, r) -> None:
        if r is None:
            raise TinyFishError("no response from TinyFish")
        if r.status_code in (401, 403):
            raise TinyFishAuthError(f"TinyFish rejected the API key (HTTP {r.status_code})")
        if r.status_code == 402:
            raise TinyFishError("TinyFish free quota exhausted (HTTP 402): add wallet balance or wait for reset")

    def search(self, query: str, **params) -> list[dict]:
        p = {"query": query}
        p.update({k: v for k, v in params.items() if v not in (None, "")})
        r = self.http.get(SEARCH_URL, params=p, headers=self._headers())
        self._check(r)
        if r.status_code != 200:
            log.warning("search %r -> HTTP %s %s", query[:60], r.status_code, r.text[:120])
            return []
        try:
            return r.json().get("results", []) or []
        except ValueError:
            return []

    def fetch(self, urls: list[str], fmt: str = "markdown") -> tuple[list[dict], list[dict]]:
        r = self.http.post(FETCH_URL, json={"urls": urls[:10], "format": fmt}, headers=self._headers())
        self._check(r)
        if r.status_code != 200:
            log.warning("fetch -> HTTP %s %s", r.status_code, r.text[:120])
            return [], [{"url": u, "error": f"HTTP {r.status_code}"} for u in urls]
        try:
            j = r.json()
        except ValueError:
            return [], []
        return j.get("results", []) or [], j.get("errors", []) or []


def build_queries(tcfg: dict, today: date | None = None) -> list[dict]:
    """Return [{query, include_domains, location, language}] shuffled deterministically per day."""
    groups = tcfg.get("domain_groups", {})
    recency = int(tcfg.get("recency_days", 14)) * 1440
    out: list[dict] = []
    for gname, domains in groups.items():
        dom = ",".join(d for d in domains if d != "fiverr.com")
        if not dom:
            continue
        for phrase in tcfg.get("phrases", []):
            for svc in tcfg.get("services", []):
                svc_q = f"({svc})" if " OR " in svc else svc
                out.append({"query": f"{phrase} {svc_q}", "include_domains": dom, "recency_minutes": recency})
    social = ",".join(d for d in groups.get("social", []) + groups.get("freelance_boards", []) if d != "fiverr.com")
    for loc in tcfg.get("locales", []):
        for q in loc.get("queries", []):
            out.append({"query": q, "include_domains": social, "location": loc["location"],
                        "language": loc["language"], "recency_minutes": recency})
    rnd = random.Random((today or date.today()).toordinal())
    rnd.shuffle(out)
    return out[: int(tcfg.get("max_queries_per_run", 400))]


def result_to_item(r: dict, location: str = "") -> RawItem | None:
    url = (r.get("url") or "").strip()
    title = (r.get("title") or "").strip()
    snippet = (r.get("snippet") or "").strip()
    if not url or not title or SELLER_URL.search(url):
        return None
    is_job = bool(re.search(r"linkedin\.com/jobs/|/jobs?/view|indeed\.|glassdoor\.|/careers?/", url, re.I))
    return RawItem(
        source="tinyfish", platform=host_platform(url), lead_type="job" if is_job else "buyer", title=title,
        text=snippet or title,
        url=url, author=r.get("site_name") or "", posted_at=None, location_hint=location,
    )


def collect(cfg: dict, http=None) -> Iterator[RawItem]:
    tcfg = cfg.get("tinyfish", {})
    if not tcfg.get("enabled", True):
        return
    client = TinyFishClient()
    if not client.enabled:
        log.warning("TINYFISH_API_KEY not set - skipping TinyFish sweep")
        return
    queries = build_queries(tcfg)
    log.info("TinyFish: running %d searches", len(queries))
    from ..classify import country_from_code

    def run(q: dict):
        params = {k: v for k, v in q.items() if k != "query"}
        try:
            res = client.search(q["query"], **params)
        except TinyFishAuthError:
            raise
        except TinyFishError as e:
            log.error("%s", e)
            return []
        loc = country_from_code(q.get("location", ""))
        return [result_to_item(r, loc) for r in res]

    seen: set[str] = set()
    with ThreadPoolExecutor(max_workers=int(tcfg.get("workers", 4))) as ex:
        for items in ex.map(run, queries):
            for it in items:
                if it and it.url not in seen:
                    seen.add(it.url)
                    yield it
