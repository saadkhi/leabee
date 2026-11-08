"""GitHub issues asking for paid help / bounties (public search API; GITHUB_TOKEN optional for higher limits)."""
from __future__ import annotations

import logging
import os
import re
from typing import Iterator
from urllib.parse import quote_plus

from ..models import RawItem
from ..textutil import parse_dt

log = logging.getLogger("leabee.github")

DEFAULT_QUERIES = [
    '"looking for a developer" is:issue is:open',
    '"hiring" "freelance" is:issue is:open',
    'label:bounty is:issue is:open',
    '"paid" "contract" "developer needed" is:issue is:open',
]


_INTERNAL_TITLE = re.compile(r"^\s*(?:docs|feat|fix|chore|refactor|build|ci|test|perf|style)(?:\(.*?\))?!?:|^\s*leads\b|^\s*\[?bug\]?|^\s*\[?feature", re.I)
_HIRE_WORDS = re.compile(r"\b(?:hiring|bounty|paid (?:work|gig|project|contract)|we(?:'ll| will) pay|willing to pay|budget|commission|"
                         r"contractor)\b|\$\s?\d{2,}", re.I)
_PROMO_TITLE = re.compile(r"^\s*spec\b|volunteers?|\bbest .{0,40}(?:company|agency|services)\b|^\s*which\b|\?\s*$|calling founding|beta\b", re.I)


def parse_issues(data: dict) -> list[RawItem]:
    items = []
    for it in data.get("items", []):
        if "pull_request" in it:
            continue
        user = it.get("user") or {}
        title = (it.get("title") or "")
        if user.get("type") == "Bot" or _INTERNAL_TITLE.search(title) or _PROMO_TITLE.search(title):
            continue                      # bot reports and repo-internal tasks are not buyers
        if not _HIRE_WORDS.search(title + " " + (it.get("body") or "")[:600]):
            continue
        items.append(RawItem(
            source="github", platform="GitHub", lead_type="buyer", title=(it.get("title") or "").strip(),
            text=(it.get("body") or "")[:4000], url=it.get("html_url", ""), author=user.get("login", ""),
            author_url=user.get("html_url", ""), posted_at=parse_dt(it.get("created_at")),
        ))
    return [i for i in items if i.url]


def collect(cfg: dict, http) -> Iterator[RawItem]:
    gcfg = cfg.get("github", {}) or {}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    seen: set[str] = set()
    for q in gcfg.get("queries", DEFAULT_QUERIES):
        r = http.get(f"https://api.github.com/search/issues?q={quote_plus(q)}&sort=created&order=desc&per_page=50",
                     headers=headers)
        if r is None or r.status_code != 200:
            log.warning("github %r -> %s", q, getattr(r, "status_code", "no response"))
            continue
        for item in parse_issues(r.json()):
            if item.url not in seen:
                seen.add(item.url)
                yield item
