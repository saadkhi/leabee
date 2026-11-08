from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"</?(p|div|br|li|h\d|tr)[^>]*>", re.I)
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_NL_RE = re.compile(r"\n\s*\n+")


def strip_html(s: str) -> str:
    if not s:
        return ""
    s = html.unescape(s)
    s = _BR_RE.sub("\n", s)
    s = _TAG_RE.sub("", s)
    s = html.unescape(s)
    s = _WS_RE.sub(" ", s)
    s = re.sub(r" ?\n ?", "\n", s)
    s = _NL_RE.sub("\n\n", s)
    return s.strip()


_TRACKING = {"fbclid", "gclid", "ref", "ref_src", "mc_cid", "mc_eid", "source", "share_id"}


def canonical_url(url: str) -> str:
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
         if not k.lower().startswith("utm_") and k.lower() not in _TRACKING]
    path = p.path.rstrip("/") or "/"
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host == "old.reddit.com":
        host = "reddit.com"
    return urlunsplit(("https", host, path, urlencode(q), ""))


def lead_id(url: str) -> str:
    return hashlib.sha1(canonical_url(url).encode("utf-8")).hexdigest()[:16]


def parse_dt(value) -> datetime | None:
    """Parse ISO-8601 / epoch into aware UTC datetime."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        s = str(value).strip()
        if s.isdigit():
            return datetime.fromtimestamp(int(s), tz=timezone.utc)
        s = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def clip(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"
