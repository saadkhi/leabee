"""Page extractors, cheapest first:  plain HTTP -> Crawl4AI (browser, free) -> TinyFish Fetch -> AI agents (AgentQL, Browser Use,
Skyvern, Stagehand; need keys). `enrich_site()` uses them to turn a company website into verified contact details."""
from __future__ import annotations

import logging
import os
from urllib.parse import urlsplit

from .agents import CONTACT_FIELDS, AgentQLBackend, BrowserUseBackend, SkyvernBackend, StagehandBackend
from .base import CONTACT_HINT, GENERIC_LOCALS, PageResult, email_domain_resolves, find_emails, find_phones, is_public_url
from .crawl4ai_backend import Crawl4AIBackend
from .plain import PlainBackend

log = logging.getLogger("leabee.extractors")


class TinyFishFetchBackend:
    name = "tinyfish"

    def available(self) -> tuple[bool, str]:
        return (True, "") if os.environ.get("TINYFISH_API_KEY") else (False, "set TINYFISH_API_KEY")

    def fetch(self, url: str, timeout: float = 40) -> PageResult:
        if not is_public_url(url):
            return PageResult(url=url, backend=self.name, error="blocked: not a public http(s) address")
        from ..collectors.tinyfish import TinyFishClient, TinyFishError
        try:
            results, errors = TinyFishClient().fetch([url])
        except TinyFishError as e:
            return PageResult(url=url, backend=self.name, error=str(e)[:200])
        if not results or not results[0].get("text"):
            return PageResult(url=url, backend=self.name, error=str(errors)[:200] or "empty")
        text = results[0]["text"]
        return PageResult(url=url, ok=True, backend=self.name, title=results[0].get("title", ""), text=text,
                          emails=find_emails(text), phones=find_phones(text))


PAGE_BACKENDS = {"plain": PlainBackend, "crawl4ai": Crawl4AIBackend, "tinyfish": TinyFishFetchBackend}
AGENT_BACKENDS = {"agentql": AgentQLBackend, "browser_use": BrowserUseBackend, "skyvern": SkyvernBackend, "stagehand": StagehandBackend}
DEFAULT_PAGE_ORDER = ("plain", "crawl4ai", "tinyfish")
DEFAULT_AGENT_ORDER = ("agentql", "stagehand", "browser_use", "skyvern")


def status() -> list[dict]:
    """What is installed/configured. Shown by `python -m leabee tools` and /api/tools."""
    out = []
    for kind, group in (("page", PAGE_BACKENDS), ("agent", AGENT_BACKENDS)):
        for name, cls in group.items():
            ok, why = cls().available()
            out.append({"name": name, "kind": kind, "ready": ok, "how_to_enable": why})
    return out


def fetch_page(url: str, order: tuple[str, ...] | list[str] = DEFAULT_PAGE_ORDER, need: str = "text") -> PageResult:
    """First backend that returns a usable page. need='contact' keeps going while no email/phone has been found."""
    best = PageResult(url=url, error="no backend available")
    for name in order:
        cls = PAGE_BACKENDS.get(name)
        if not cls:
            continue
        b = cls()
        ok, _ = b.available()
        if not ok:
            continue
        page = b.fetch(url)
        if page.ok and len(page.text) > 80:
            if need != "contact" or page.emails or page.phones:
                return page
            if not best.ok:
                best = page
        elif not best.ok:
            best = page
    return best


def _contact_links(page: PageResult, host: str, limit: int = 2) -> list[str]:
    out = []
    for l in page.links:
        u = urlsplit(l)
        if (u.hostname or "").lower().removeprefix("www.") == host and CONTACT_HINT.search(u.path) and l not in out and l.split("#")[0] != page.url:
            out.append(l.split("#")[0])
        if len(out) >= limit:
            break
    return out


def best_email(emails: list[str], website: str = "") -> str:
    """Prefer a named mailbox on the company's own domain; generic info@ comes last."""
    if not emails:
        return ""
    own = (urlsplit(website).hostname or "").lower().removeprefix("www.")
    return sorted(emails, key=lambda e: (e.split("@")[0] in GENERIC_LOCALS, own not in e.split("@")[1], len(e)))[0]


def enrich_site(url: str, cfg: dict | None = None, use_agents: bool = False) -> dict:
    """Crawl a company website (home + contact/about pages) and return verified contact details."""
    cfg = cfg or {}
    order = tuple((cfg.get("extractors") or {}).get("page_order") or DEFAULT_PAGE_ORDER)
    home = fetch_page(url, order, need="contact")
    info = {"website": url, "ok": home.ok, "backend": home.backend, "title": home.title, "emails": [], "phones": [], "socials": dict(home.socials),
            "pages": [home.url] if home.ok else [], "error": "" if home.ok else home.error}
    emails, phones = list(home.emails), list(home.phones)
    if home.ok:
        host = (urlsplit(home.url).hostname or "").lower().removeprefix("www.")
        for link in _contact_links(home, host):
            sub = fetch_page(link, order, need="contact")
            if sub.ok:
                info["pages"].append(sub.url)
                emails += sub.emails
                phones += sub.phones
                info["socials"] = {**sub.socials, **info["socials"]}
    if not emails and not phones and use_agents:
        for name in DEFAULT_AGENT_ORDER:
            b = AGENT_BACKENDS[name]()
            if not b.available()[0]:
                continue
            try:
                data = b.extract(url, "find how to contact this business", CONTACT_FIELDS)
            except Exception as e:      # noqa: BLE001
                log.warning("%s failed on %s: %s", name, url, e)
                continue
            emails += find_emails(" ".join(map(str, data.get("emails") or [])))
            phones += find_phones(" ".join(map(str, data.get("phones") or [])))
            if data.get("linkedin"):
                info["socials"].setdefault("linkedin", str(data["linkedin"]))
            info["owner_or_contact_name"] = data.get("owner_or_contact_name") or ""
            info["services"] = data.get("services") or ""
            info["backend"] = (info["backend"] + "+" + name).strip("+")
            break
    emails = list(dict.fromkeys(emails))
    info["emails"] = [{"email": e, "generic": e.split("@")[0] in GENERIC_LOCALS, "domain_ok": email_domain_resolves(e)} for e in emails[:6]]
    good = [e["email"] for e in info["emails"] if e["domain_ok"]]
    info["best_email"] = best_email(good, url)
    info["phones"] = list(dict.fromkeys(phones))[:4]
    return info
