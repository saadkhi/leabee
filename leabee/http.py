from __future__ import annotations

import logging
import os
import threading
import time
from urllib.parse import urlsplit

import requests

log = logging.getLogger("leabee.http")


class Http:
    """requests wrapper with per-host pacing, retries and a polite User-Agent."""

    def __init__(self, delay: float = 1.5, timeout: float = 25, retries: int = 4):
        self.delay = delay
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        contact = os.environ.get("LEABEE_CONTACT", "").strip()
        ua = "Mozilla/5.0 (compatible; leabee/1.0; lead research tool"
        ua += f"; {contact})" if contact else ")"
        self.session.headers.update({"User-Agent": ua, "Accept": "*/*"})
        self._last: dict[str, float] = {}
        self._cool: dict[str, float] = {}
        self._lock = threading.Lock()

    def _pace(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            wait = max(self._last.get(host, 0) + self.delay - now, self._cool.get(host, 0) - now)
            self._last[host] = now + max(wait, 0)
        if wait > 0:
            time.sleep(wait)

    def _note_limits(self, host: str, r) -> None:
        """Honour X-RateLimit-* headers (Reddit sends them): sleep out the window when exhausted."""
        try:
            remaining = float(r.headers.get("x-ratelimit-remaining", "nan"))
            reset = float(r.headers.get("x-ratelimit-reset", "nan"))
        except ValueError:
            return
        if remaining <= 1 and reset == reset:   # reset is NaN-safe check
            with self._lock:
                self._cool[host] = time.monotonic() + min(reset, 120) + 1

    def request(self, method: str, url: str, **kw) -> requests.Response | None:
        host = urlsplit(url).netloc
        kw.setdefault("timeout", self.timeout)
        for attempt in range(1, self.retries + 1):
            self._pace(host)
            try:
                r = self.session.request(method, url, **kw)
            except requests.RequestException as e:
                log.warning("%s %s failed (%s) attempt %d", method, url[:90], e.__class__.__name__, attempt)
                time.sleep(1.5 * attempt)
                continue
            self._note_limits(host, r)
            if r.status_code == 429 or r.status_code >= 500:
                retry_after = r.headers.get("Retry-After", "")
                reset = r.headers.get("x-ratelimit-reset", "")
                if retry_after.isdigit():
                    wait = int(retry_after)
                elif reset.replace(".", "", 1).isdigit():
                    wait = float(reset) + 1
                else:
                    wait = 4 * attempt
                log.warning("%s %s -> %s, waiting %ss", method, url[:90], r.status_code, int(wait))
                time.sleep(min(wait, 60))
                continue
            return r
        return None

    def get(self, url: str, **kw):
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw):
        return self.request("POST", url, **kw)
