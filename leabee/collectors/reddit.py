"""Reddit via public Atom/RSS feeds (no login, no API key)."""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from typing import Iterator
from urllib.parse import quote_plus

from ..models import RawItem
from ..textutil import parse_dt, strip_html

log = logging.getLogger("leabee.reddit")
NS = {"a": "http://www.w3.org/2005/Atom"}

# Title flair conventions used by hiring subreddits.
_SELLER_FLAIR = re.compile(r"^\s*[\[\(]\s*(for hire|offer|offering|available|looking for work|forhire)\s*[\]\)]", re.I)
_BUYER_FLAIR = re.compile(r"^\s*[\[\(]\s*(hiring|task|paid|request|looking for|wanted|job|commission|bounty)\s*[\]\)]", re.I)


def parse_feed(xml_text: str, default_sub: str = "") -> list[RawItem]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        log.warning("bad reddit feed: %s", e)
        return []
    items: list[RawItem] = []
    for e in root.findall("a:entry", NS):
        title = (e.findtext("a:title", "", NS) or "").strip()
        link_el = e.find("a:link", NS)
        url = link_el.get("href", "") if link_el is not None else ""
        if not url or "/comments/" not in url:
            continue
        author = (e.findtext("a:author/a:name", "", NS) or "").strip()
        author_url = (e.findtext("a:author/a:uri", "", NS) or "").strip()
        cat = e.find("a:category", NS)
        sub = cat.get("label", "") if cat is not None else (f"r/{default_sub}" if default_sub else "")
        content = e.findtext("a:content", "", NS) or ""
        text = strip_html(content)
        # Reddit appends "submitted by /u/x [link] [comments]" boilerplate.
        text = re.sub(r"\s*submitted by\s+/u/\S+\s*(\[link\]\s*)?(\[comments\])?\s*$", "", text, flags=re.I).strip()
        posted = parse_dt(e.findtext("a:published", "", NS) or e.findtext("a:updated", "", NS))
        force = ""
        if _SELLER_FLAIR.match(title):
            force = "seller"
        elif _BUYER_FLAIR.match(title):
            force = "buyer"
        items.append(RawItem(
            source="reddit", platform="Reddit", lead_type="buyer", title=title, text=text, url=url,
            author=author, author_url=author_url, posted_at=posted, force_intent=force,
            location_hint="",
        ))
    return items


def collect(cfg: dict, http) -> Iterator[RawItem]:
    rcfg = cfg.get("reddit", {})
    seen: set[str] = set()

    def emit(items: list[RawItem], buyer_only: bool = False):
        for it in items:
            if it.url in seen:
                continue
            seen.add(it.url)
            if buyer_only and it.force_intent != "seller":
                it.force_intent = "buyer"
            yield it

    for sub in rcfg.get("subreddits", []):
        name = sub["name"] if isinstance(sub, dict) else sub
        buyer_only = bool(sub.get("buyer_only")) if isinstance(sub, dict) else False
        r = http.get(f"https://www.reddit.com/r/{name}/new/.rss?limit=100")
        if r is None or r.status_code != 200:
            log.warning("r/%s -> %s", name, getattr(r, "status_code", "no response"))
            continue
        yield from emit(parse_feed(r.text, name), buyer_only)

    window = rcfg.get("search_window", "week")
    for q in rcfg.get("search_queries", []):
        r = http.get(f"https://www.reddit.com/search.rss?q={quote_plus(q)}&sort=new&t={window}&limit=100")
        if r is None or r.status_code != 200:
            log.warning("reddit search %r -> %s", q, getattr(r, "status_code", "no response"))
            continue
        yield from emit(parse_feed(r.text))
