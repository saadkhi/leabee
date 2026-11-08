"""Hacker News via the free Algolia API: monthly hiring threads + intent search."""
from __future__ import annotations

import logging
import re
import time
from typing import Iterator

from ..models import RawItem
from ..textutil import parse_dt, strip_html

log = logging.getLogger("leabee.hn")
ALGOLIA = "https://hn.algolia.com/api/v1"
ITEM_URL = "https://news.ycombinator.com/item?id={}"


def _find_threads(http, query: str, n: int) -> list[dict]:
    r = http.get(f"{ALGOLIA}/search_by_date", params={
        "query": query, "tags": "story,author_whoishiring", "hitsPerPage": n * 3})
    if r is None or r.status_code != 200:
        return []
    hits = [h for h in r.json().get("hits", []) if query.lower().split("?")[0] in (h.get("title") or "").lower()]
    return hits[:n]


def _walk_top_level(item: dict):
    for ch in item.get("children", []) or []:
        if ch.get("text"):
            yield ch


def parse_freelancer_comment(c: dict) -> RawItem | None:
    text = strip_html(c.get("text") or "")
    if not text:
        return None
    head = text.split("\n", 1)[0]
    if re.match(r"^\s*seeking\s+work", head, re.I):
        return None
    force = "buyer" if re.match(r"^\s*seeking\s+freelancer", head, re.I) else ""
    return RawItem(
        source="hackernews", platform="Hacker News", lead_type="buyer", title=head[:200], text=text,
        url=ITEM_URL.format(c["id"]), author=c.get("author") or "",
        author_url=f"https://news.ycombinator.com/user?id={c.get('author')}" if c.get("author") else "",
        posted_at=parse_dt(c.get("created_at")), force_intent=force, location_hint=head,
    )


def parse_hiring_comment(c: dict) -> RawItem | None:
    text = strip_html(c.get("text") or "")
    if not text or not re.search(r"\bremote\b", text, re.I):
        return None
    head = text.split("\n", 1)[0]
    return RawItem(
        source="hackernews", platform="Hacker News", lead_type="job", title=head[:200], text=text,
        url=ITEM_URL.format(c["id"]), author=c.get("author") or "",
        author_url=f"https://news.ycombinator.com/user?id={c.get('author')}" if c.get("author") else "",
        posted_at=parse_dt(c.get("created_at")), location_hint=head,
    )


def collect(cfg: dict, http) -> Iterator[RawItem]:
    hcfg = cfg.get("hackernews", {})
    n = int(hcfg.get("threads_back", 3))
    for query, parser in (("Freelancer? Seeking freelancer?", parse_freelancer_comment),
                          ("Who is hiring?", parse_hiring_comment)):
        for thread in _find_threads(http, query, n):
            r = http.get(f"{ALGOLIA}/items/{thread['objectID']}", timeout=60)
            if r is None or r.status_code != 200:
                continue
            for ch in _walk_top_level(r.json()):
                it = parser(ch)
                if it:
                    yield it
    days = int(hcfg.get("query_days", 30))
    since = int(time.time()) - days * 86400
    for q in hcfg.get("queries", []):
        for tags in ("comment", "story"):
            r = http.get(f"{ALGOLIA}/search_by_date", params={
                "query": q, "tags": tags, "numericFilters": f"created_at_i>{since}", "hitsPerPage": 100})
            if r is None or r.status_code != 200:
                continue
            for h in r.json().get("hits", []):
                body = strip_html(h.get("comment_text") or h.get("story_text") or "")
                title = h.get("story_title") or h.get("title") or body[:120]
                oid = h.get("objectID")
                if not oid:
                    continue
                yield RawItem(
                    source="hackernews", platform="Hacker News", lead_type="buyer", title=title[:200],
                    text=body or title, url=ITEM_URL.format(oid), author=h.get("author") or "",
                    author_url=f"https://news.ycombinator.com/user?id={h.get('author')}" if h.get("author") else "",
                    posted_at=parse_dt(h.get("created_at")),
                )
