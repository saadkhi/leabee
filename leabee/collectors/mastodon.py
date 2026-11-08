"""Mastodon public hashtag timelines (free, no auth)."""
from __future__ import annotations

import logging
import re
from typing import Iterator

from ..models import RawItem
from ..textutil import parse_dt, strip_html

log = logging.getLogger("leabee.mastodon")


def parse_status(s: dict, instance: str) -> RawItem | None:
    if s.get("reblog"):
        s = s["reblog"]
    text = strip_html(s.get("content") or "")
    if not text or s.get("sensitive") and not text:
        return None
    acct = s.get("account", {})
    if re.search(r"jobs?|hiring|careers?|vacanc|recruit", acct.get("acct") or "", re.I):
        return None            # job-feed bots post employee roles, not buyers
    tags = [t.get("name", "") for t in s.get("tags", [])]
    if "forhire" in tags and not any(t in tags for t in ("hiring", "needdeveloper", "lookingfordeveloper")):
        force = "seller"
    else:
        force = ""
    return RawItem(
        source="mastodon", platform="Mastodon", lead_type="buyer", title=text.split("\n", 1)[0][:200], text=text,
        url=s.get("url") or s.get("uri") or "", author=acct.get("acct") or "", author_url=acct.get("url") or "",
        posted_at=parse_dt(s.get("created_at")), force_intent=force, extra_tags=[],
    )


def collect(cfg: dict, http) -> Iterator[RawItem]:
    m = cfg.get("mastodon", {})
    for inst in m.get("instances", ["mastodon.social"]):
        for tag in m.get("hashtags", []):
            r = http.get(f"https://{inst}/api/v1/timelines/tag/{tag}", params={"limit": 40})
            if r is None or r.status_code != 200:
                log.warning("mastodon %s #%s -> %s", inst, tag, getattr(r, "status_code", "no response"))
                continue
            try:
                data = r.json()
            except ValueError:
                continue
            for s in data:
                it = parse_status(s, inst)
                if it and it.url:
                    yield it
