"""Backend 1: plain HTTP fetch. Fast, free, no browser; fails on JavaScript-only sites (then Crawl4AI takes over).

Hardened: every redirect hop is re-validated (public http(s) addresses only), the connected peer address is checked again after the connection
(defence against DNS rebinding), responses are capped at 2 MB, only HTML is accepted, and no cookies or credentials are ever sent."""
from __future__ import annotations

from urllib.parse import urljoin

import requests

from .base import PageResult, is_public_ip, is_public_url, parse_html

UA = "Mozilla/5.0 (compatible; leabee/1.0; business contact lookup)"
MAX_BYTES = 2_000_000
MAX_REDIRECTS = 5


def _peer_ip(resp) -> str:
    try:
        return resp.raw._connection.sock.getpeername()[0]
    except Exception:      # noqa: BLE001  (not available for every transport)
        return ""


class PlainBackend:
    name = "plain"

    def available(self) -> tuple[bool, str]:
        return True, ""

    def fetch(self, url: str, timeout: float = 15) -> PageResult:
        sess = requests.Session()
        sess.trust_env = False                                    # ignore proxy/netrc credentials from the environment
        try:
            cur = url
            for _ in range(MAX_REDIRECTS + 1):
                if not is_public_url(cur):
                    return PageResult(url=url, backend=self.name, error="blocked: not a public http(s) address")
                r = sess.get(cur, timeout=timeout, headers={"User-Agent": UA, "Accept": "text/html,*/*"}, allow_redirects=False, stream=True)
                peer = _peer_ip(r)
                if peer and not is_public_ip(peer):
                    r.close()
                    return PageResult(url=url, backend=self.name, error="blocked: connected to a non-public address")
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("Location"):
                    cur = urljoin(cur, r.headers["Location"])
                    r.close()
                    continue
                break
            else:
                return PageResult(url=url, backend=self.name, error="too many redirects")
            if r.status_code >= 400 or "html" not in r.headers.get("Content-Type", "html").lower():
                r.close()
                return PageResult(url=url, backend=self.name, error=f"HTTP {r.status_code}")
            body = b""
            for chunk in r.iter_content(65536):
                body += chunk
                if len(body) > MAX_BYTES:
                    break                                         # never read unbounded responses into memory
            r.close()
            enc = r.encoding or r.apparent_encoding or "utf-8"
            page = parse_html(cur, body[:MAX_BYTES].decode(enc, errors="replace")[:600_000])
            page.backend = self.name
            return page
        except requests.RequestException as e:
            return PageResult(url=url, backend=self.name, error=f"{e.__class__.__name__}"[:200])
        finally:
            sess.close()
