from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class RawItem:
    """What a collector yields, before classification."""
    source: str                     # reddit | hackernews | remoteok | tinyfish ...
    platform: str                   # human platform name
    lead_type: str                  # "buyer" (person asking) | "job" (posted opening)
    title: str
    text: str
    url: str
    author: str = ""
    author_url: str = ""
    posted_at: Optional[datetime] = None   # timezone-aware UTC, None if unknown
    force_intent: str = ""          # "buyer" | "seller" | "" (let classifier decide)
    location_hint: str = ""
    extra_tags: list = field(default_factory=list)
    contact_hint: str = ""
    meta: dict = field(default_factory=dict)   # source-specific record (e.g. a Google Maps business)
