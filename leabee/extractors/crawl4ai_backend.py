"""Backend 2: Crawl4AI (https://github.com/unclecode/crawl4ai). A real browser, so JavaScript sites work; free and local.

Install:  pip install crawl4ai   (then either `crawl4ai-setup` to download Chromium, or just set LEABEE_BROWSER_CHANNEL=msedge
          / chrome to reuse the browser already on your machine, no download needed).
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading

from .base import PageResult, find_emails, find_phones, find_socials, is_public_url, parse_html


def _default_channel() -> str:
    explicit = os.environ.get("LEABEE_BROWSER_CHANNEL", "").strip()
    if explicit:
        return explicit
    return "msedge" if sys.platform == "win32" else "chromium"


class Crawl4AIBackend:
    name = "crawl4ai"

    def available(self) -> tuple[bool, str]:
        try:
            import crawl4ai  # noqa: F401
        except ImportError:
            return False, "pip install crawl4ai"
        return True, ""

    def _run(self, coro):
        """Run an async crawl from sync code, also when an event loop is already running in this thread."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        box: dict = {}

        def target():
            try:
                box["v"] = asyncio.run(coro)
            except BaseException as e:      # noqa: BLE001
                box["e"] = e
        t = threading.Thread(target=target)
        t.start()
        t.join()
        if "e" in box:
            raise box["e"]
        return box["v"]

    async def _crawl(self, url: str, wait_s: float) -> PageResult:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

        channel = _default_channel()
        bcfg = BrowserConfig(headless=True, verbose=False, **({"chrome_channel": channel, "channel": channel} if channel != "chromium" else {}))
        rcfg = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=int(wait_s * 1000), verbose=False)
        async with AsyncWebCrawler(config=bcfg) as crawler:
            res = await crawler.arun(url=url, config=rcfg)
        if not getattr(res, "success", False):
            return PageResult(url=url, backend=self.name, error=(getattr(res, "error_message", "") or "crawl failed")[:200])
        final = getattr(res, "url", url) or url
        if not is_public_url(final):
            return PageResult(url=url, backend=self.name, error="blocked: redirected to a non-public address")
        html = getattr(res, "html", "") or ""
        md = getattr(res, "markdown", "")
        text = str(getattr(md, "raw_markdown", md) or "")
        page = parse_html(getattr(res, "url", url) or url, html) if html else PageResult(url=url)
        page.backend = self.name
        if text.strip():
            page.text = text
            page.ok = True
            page.emails = list(dict.fromkeys(page.emails + find_emails(text)))
            page.phones = list(dict.fromkeys(page.phones + find_phones(text)))[:5]
        links = []
        for group in (getattr(res, "links", None) or {}).values():
            links += [l.get("href", "") for l in group if isinstance(l, dict)]
        if links:
            page.links = list(dict.fromkeys(page.links + [l for l in links if l.startswith("http")]))
            page.socials = {**find_socials(page.links), **page.socials}
        return page

    def fetch(self, url: str, timeout: float = 30) -> PageResult:
        if not is_public_url(url):
            return PageResult(url=url, backend=self.name, error="blocked: not a public http(s) address")
        try:
            return self._run(self._crawl(url, timeout))
        except Exception as e:      # browser missing, crashed, timed out
            return PageResult(url=url, backend=self.name, error=f"{e.__class__.__name__}: {str(e)[:160]}")
