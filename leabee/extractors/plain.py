"""Backend 1: plain HTTP fetch. Fast, free, no browser; fails on JavaScript-only sites (then Crawl4AI takes over)."""
from __future__ import annotations

import requests

from .base import PageResult, is_public_url, parse_html

UA = "Mozilla/5.0 (compatible; leabee/1.0; business contact lookup)"


class PlainBackend:
    name = "plain"

    def available(self) -> tuple[bool, str]:
        return True, ""

    def fetch(self, url: str, timeout: float = 15) -> PageResult:
        if not is_public_url(url):
            return PageResult(url=url, backend=self.name, error="blocked: not a public http(s) address")
        try:
            r = requests.get(url, timeout=timeout, headers={"User-Agent": UA, "Accept": "text/html,*/*"}, allow_redirects=True)
        except requests.RequestException as e:
            return PageResult(url=url, backend=self.name, error=f"{e.__class__.__name__}: {e}"[:200])
        if not is_public_url(r.url):                 # a redirect must not lead somewhere internal
            return PageResult(url=url, backend=self.name, error="blocked: redirected to a non-public address")
        if r.status_code >= 400 or "html" not in r.headers.get("Content-Type", "html").lower():
            return PageResult(url=url, backend=self.name, error=f"HTTP {r.status_code}")
        page = parse_html(r.url, r.text[:600_000])
        page.backend = self.name
        return page
