"""Shared types and helpers for page extractors (plain HTTP, Crawl4AI, TinyFish Fetch, and the AI browser agents)."""
from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from ..classify import EMAIL_RE, _BAD_EMAIL_HOST, _BAD_EMAIL_TAIL, _BAD_EMAIL_USER

SOCIAL_HOSTS = {
    "linkedin.com": "linkedin", "twitter.com": "twitter", "x.com": "twitter", "facebook.com": "facebook", "instagram.com": "instagram",
    "youtube.com": "youtube", "github.com": "github", "t.me": "telegram", "wa.me": "whatsapp", "tiktok.com": "tiktok", "behance.net": "behance",
    "dribbble.com": "dribbble", "pinterest.com": "pinterest",
}
GENERIC_LOCALS = {"info", "contact", "hello", "hi", "support", "admin", "sales", "office", "mail", "enquiries", "inquiries", "team", "help"}
_PHONE = re.compile(r"(?<![\w.])(\+\d{1,3}[\s.\-]?\(?\d{1,4}\)?(?:[\s.\-]?\d{2,4}){2,4}|\(\d{3}\)\s?\d{3}[\s.\-]\d{4}|\d{3}[\s.\-]\d{3}[\s.\-]\d{4})(?![\w])")
_HREF = re.compile(r"""href\s*=\s*["']([^"'#][^"']*)["']""", re.I)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
CONTACT_HINT = re.compile(r"contact|about|team|company|reach|get-in-touch|support|impressum|kontakt", re.I)


@dataclass
class PageResult:
    url: str
    ok: bool = False
    backend: str = ""
    title: str = ""
    text: str = ""                                  # readable text / markdown
    html: str = ""
    links: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    socials: dict[str, str] = field(default_factory=dict)
    error: str = ""


def is_public_ip(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    return not (a.is_private or a.is_loopback or a.is_link_local or a.is_reserved or a.is_multicast or a.is_unspecified)


def is_public_url(url: str) -> bool:
    """SSRF guard: only http(s) to public internet addresses. Lead URLs come from scraped content, so never trust them."""
    try:
        u = urlsplit(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            return False
        host = u.hostname
        if host.lower() in ("localhost",) or host.endswith((".local", ".internal", ".localhost")):
            return False
        infos = socket.getaddrinfo(host, u.port or (443 if u.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
        for info in infos:
            if not is_public_ip(info[4][0]):
                return False
        return True
    except (ValueError, OSError):
        return False


def find_emails(text: str) -> list[str]:
    out, seen = [], set()
    for e in EMAIL_RE.findall(text or ""):
        el = e.lower().rstrip(".")
        user, _, host = el.partition("@")
        if el.endswith(_BAD_EMAIL_TAIL) or host in _BAD_EMAIL_HOST or user in _BAD_EMAIL_USER or el in seen:
            continue
        if re.search(r"\d+x\.(?:png|jpg)|@\d+x", el):
            continue
        seen.add(el)
        out.append(el)
    # named people first, generic mailboxes (info@, contact@) last
    return sorted(out, key=lambda e: e.split("@")[0] in GENERIC_LOCALS)


def find_phones(text: str) -> list[str]:
    out, seen = [], set()
    for m in _PHONE.finditer(text or ""):
        p = re.sub(r"\s+", " ", m.group(1)).strip()
        digits = re.sub(r"\D", "", p)
        if not 8 <= len(digits) <= 15 or len(set(digits)) <= 2 or p in seen:
            continue
        # drop things that are really dates / ids: 2026-11-08, 1234567890123
        if re.fullmatch(r"(?:19|20)\d{2}[\-./]\d{1,2}[\-./]\d{1,2}", p) or re.fullmatch(r"\d{13,}", digits) and "+" not in p:
            continue
        seen.add(p)
        out.append(p)
    return out[:5]


def find_socials(links: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for link in links:
        host = (urlsplit(link).hostname or "").lower().removeprefix("www.")
        kind = SOCIAL_HOSTS.get(host)
        path = urlsplit(link).path.strip("/")
        if not kind or kind in out or not path or re.match(r"(?:share|intent|sharer|dialog|plugins|login|signup|home)\b", path, re.I):
            continue
        out[kind] = link.split("?")[0]
    return out


def parse_html(url: str, html: str) -> PageResult:
    """Turn raw HTML into a PageResult (title, readable text, links, emails, phones, socials)."""
    from ..textutil import strip_html

    m = _TITLE.search(html or "")
    links = []
    for h in _HREF.findall(html or ""):
        h = h.strip()
        if h.lower().startswith(("javascript:", "data:")):
            continue
        links.append(h if h.lower().startswith(("mailto:", "tel:")) else urljoin(url, h))
    body = re.sub(r"(?is)<(script|style|noscript|svg)\b.*?</\1>", " ", html or "")
    text = strip_html(body)[:200_000]                  # cap what the regexes below have to scan
    mailtos = [h[7:].split("?")[0] for h in links if h.lower().startswith("mailto:")]
    tels = [re.sub(r"[^\d+]", "", h[4:]) for h in links if h.lower().startswith("tel:")]
    emails = find_emails(" ".join(mailtos) + " " + text)
    phones = [("+" + t.lstrip("+")) if t.startswith("+") else t for t in tels if 8 <= len(re.sub(r"\D", "", t)) <= 15] + find_phones(text)
    return PageResult(url=url, ok=bool(text.strip()), title=re.sub(r"\s+", " ", strip_html(m.group(1))).strip() if m else "", text=text, html=html,
                      links=[l for l in links if l.lower().startswith("http")], emails=emails,
                      phones=list(dict.fromkeys(phones))[:5], socials=find_socials(links))


def email_domain_resolves(email: str) -> bool:
    """Cheap sanity check that the mail domain exists (not a full MX/mailbox verification)."""
    domain = email.rpartition("@")[2]
    try:
        import dns.resolver  # optional: real MX lookup when dnspython is installed
        try:
            dns.resolver.resolve(domain, "MX", lifetime=4)
            return True
        except Exception:
            pass
    except ImportError:
        pass
    try:
        socket.getaddrinfo(domain, None)
        return True
    except OSError:
        return False
