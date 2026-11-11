"""Structured details pulled out of a post: who, what, how paid, how long, by when, with which tools.

Everything is rule-based (fast, free, offline) and only returns fields it actually found, so an empty field never
pretends to be data. Stored as JSON in leads.details and shown as chips in the dashboard.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .scoring import SKILL_RE, relative_age_hours

I = re.IGNORECASE

_PLATFORM_HOSTS = ("reddit.com", "redd.it", "redditmedia", "upwork.com", "freelancer.com", "facebook.com", "fb.com", "fb.me", "linkedin.com",
                   "twitter.com", "x.com", "t.co", "t.me", "instagram.com", "youtube.com", "youtu.be", "github.com", "news.ycombinator.com",
                   "imgur.com", "giphy.com", "discord.gg", "discord.com", "wa.me", "bit.ly", "google.com", "docs.google.com", "forms.gle",
                   "calendly.com", "mastodon", "medium.com", "peopleperhour.com", "guru.com", "remoteok.com", "weworkremotely.com",
                   "himalayas.app", "remotive.com", "workforcenow.adp.com", "greenhouse.io", "lever.co", "ashbyhq.com", "apple.com", "wikipedia.org")
_URL = re.compile(r"https?://[^\s<>\")\]]+", I)

_PAY = [
    ("fixed", re.compile(r"\bfixed[- ]price\b|\bfixed (?:fee|budget|rate)\b|\bone[- ]time (?:fee|payment)\b", I)),
    ("hourly", re.compile(r"\bhourly\b|/\s?hr\b|\bper hour\b|\bp/h\b|\ban hour\b", I)),
    ("monthly", re.compile(r"/\s?(?:mo|month)\b|\bper month\b|\bmonthly (?:retainer|fee|pay|salary)\b|\ba month\b|\bretainer\b", I)),
    ("per-unit", re.compile(r"\bper (?:word|article|video|post|page|project|lead|task)\b", I)),
]
_HOURS = re.compile(r"\b((?:less than|more than|about|around|up to|~)?\s*\d{1,2}(?:\s*[-\u2013to]+\s*\d{1,2})?\+?\s*(?:hrs?|hours?)\s*(?:/|per|a)\s*week)\b", I)
_DURATION = re.compile(r"\b((?:less than|more than|about|around)?\s*\d+\s*(?:to|-|\u2013)\s*\d+\s*(?:months?|weeks?)|(?:less than|more than)\s+\d+\s+(?:months?|weeks?|years?)|"
                       r"\d+\s*(?:months?|weeks?)\s*(?:contract|project|engagement)?|long[- ]term|short[- ]term|one[- ]time|ongoing)\b", I)
_DEADLINE = re.compile(r"\b(?:deadline(?:\s+is)?\s*[:\-]?\s*|due(?:\s+(?:on|by))?\s+|needed by\s+|by\s+|before\s+|until\s+)"
                       r"((?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s*\d{4})?)|"
                       r"(?:\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*(?:\s+\d{4})?)|"
                       r"(?:\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)|"
                       r"(?:(?:next|this)\s+(?:week|month|monday|tuesday|wednesday|thursday|friday|weekend))|tomorrow|tonight|end of (?:the )?(?:week|month|day|year|q[1-4]))\b", I)
_WITHIN = re.compile(r"\b(?:within|in (?:the next)?|inside)\s+(\d{1,3})\s*(hours?|days?|weeks?)\b", I)
_EXPERIENCE = re.compile(r"\b(entry[- ]level|intermediate|expert|senior|junior|mid[- ]level|beginner)\b|\b(\d{1,2})\+?\s*years?(?:\s+of)?\s+experience\b", I)
_TZ = re.compile(r"\b(EST|EDT|PST|PDT|CST|CDT|MST|CET|CEST|GMT(?:[+-]\d{1,2})?|UTC(?:[+-]\d{1,2})?|IST|PKT|AEST|AEDT|SAST|WAT|"
                 r"US (?:Pacific|Eastern|Central|Mountain)(?: time| hours)?|(?:EU|UK|European) (?:time ?zone|hours))\b")
_LANG_REQ = re.compile(r"\b(native (?:\w+\s+)?speaker|fluent (?:in )?[A-Z][a-z]+|(?:excellent|strong|perfect) (?:written )?(?:english|spanish|german|french|arabic) (?:skills|communication)?)\b", I)
_VOLUME = re.compile(r"\b(\d{1,4})\+?\s*(videos|articles|blog posts|posts|pages|clips|products|projects|designs|logos|leads|emails|reels|shorts|episodes)\b", I)
_POSTED = re.compile(r"\bposted\s+((?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4})", I)
_COMPANY_COLON = re.compile(r"^\s*([A-Z0-9][\w&.\-' ]{1,40}?)\s*:\s+\S")
_COMPANY_PIPE = re.compile(r"^\s*([A-Z0-9][\w&.\-' ]{1,40}?)\s*\|\s*\S")
_COMPANY_AT = re.compile(r"\bat\s+([A-Z][\w&.\-']*(?:\s+[A-Z][\w&.\-']*){0,3})\s*$")
_ARRANGE = [("remote", re.compile(r"\bremote(?:ly)?\b|\bwork from home\b|\bwfh\b|\banywhere\b", I)),
            ("hybrid", re.compile(r"\bhybrid\b", I)), ("onsite", re.compile(r"\bon-?site\b|\bin[- ]office\b", I))]
_MONTHS = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip(" .,;:-")


def parse_posted_date(text: str) -> datetime | None:
    m = _POSTED.search(text or "")
    if not m:
        return None
    parts = re.match(r"([a-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})", m.group(1), I)
    mon = _MONTHS.get(parts.group(1)[:3].lower()) if parts else None
    if not mon:
        return None
    try:
        return datetime(int(parts.group(3)), mon, int(parts.group(2)), tzinfo=timezone.utc)
    except ValueError:
        return None


def find_website(text: str) -> str:
    for u in _URL.findall(text or ""):
        u = u.rstrip(".,;:!?)`'*_\\")
        host = (urlsplit(u).hostname or "").lower().removeprefix("www.")
        if host and "." in host and not any(h in host for h in _PLATFORM_HOSTS):
            return f"https://{host}"
    return ""


def extract_company(title: str, platform: str = "", author: str = "") -> str:
    t = (title or "").strip()
    if "|" in t:
        m = _COMPANY_PIPE.match(t)
        if m and not re.search(r"\b(?:remote|hiring|developer|engineer|designer|writer)\b", m.group(1), I):
            return _clean(m.group(1))
    m = _COMPANY_COLON.match(t)
    if m and not re.match(r"(?:re|fwd|hiring|task|paid|urgent|need|wanted|looking|summary|job|note|titre|title|subject|titel|titulo|título|asunto|objet|"
                          r"hello|hi|hey|update|edit|question|help|project|position|role|remote|freelance|contract)$", m.group(1), I):
        return _clean(m.group(1))
    m = _COMPANY_AT.search(t)
    if m:
        return _clean(m.group(1))
    return ""


def extract_details(title: str, text: str, platform: str = "", author: str = "", now: datetime | None = None) -> dict:
    blob = f"{title}\n{text}"
    d: dict = {}
    comp = extract_company(title, platform, author)
    if comp:
        d["company"] = comp
    site = find_website(text)
    if site:
        d["website"] = site
    for name, rx in _PAY:
        if rx.search(blob):
            d["pay_type"] = name
            break
    m = _HOURS.search(blob)
    if m:
        d["hours"] = _clean(m.group(1))
    m = _DURATION.search(blob)
    if m:
        d["duration"] = _clean(m.group(1)).lower()
    m = _DEADLINE.search(blob)
    if m:
        d["deadline"] = _clean(m.group(1))
    else:
        w = _WITHIN.search(blob)
        if w:
            d["deadline"] = f"within {w.group(1)} {w.group(2).lower()}"
        elif re.search(r"\b(?:asap|urgent(?:ly)?|immediately|right away)\b", blob, I):
            d["deadline"] = "ASAP"
    m = _EXPERIENCE.search(blob)
    if m:
        d["experience"] = _clean(m.group(1) or f"{m.group(2)}+ years").lower()
    m = _TZ.search(blob)
    if m:
        d["timezone"] = m.group(1)
    m = _LANG_REQ.search(blob)
    if m:
        d["language_required"] = _clean(m.group(1)).lower()
    m = _VOLUME.search(blob)
    if m:
        d["volume"] = f"{m.group(1)} {m.group(2).lower()}"
    for name, rx in _ARRANGE:
        if rx.search(blob):
            d["arrangement"] = name
            break
    skills = []
    for s in SKILL_RE.findall(blob):
        k = s.lower()
        if k not in skills:
            skills.append(k)
    if skills:
        d["tech"] = skills[:10]
    posted = parse_posted_date(blob)
    if posted:
        d["posted_date"] = posted.date().isoformat()
    else:
        age = relative_age_hours(text)
        if age is not None and now is not None:
            d["posted_estimate"] = (now - __import__("datetime").timedelta(hours=age)).isoformat(timespec="hours")
    return d
