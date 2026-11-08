"""Optional AI second opinion on borderline leads (needs ANTHROPIC_API_KEY; skipped otherwise).

Rule-based scoring is fast and free but imperfect. For leads scoring in a middle band, a small Claude model
decides "is this person actually looking to pay for a remote service?" and writes a one-line summary.
"""
from __future__ import annotations

import json
import logging
import os
import re

import requests

from .storage import Store

log = logging.getLogger("leabee.ai")
API = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-haiku-5-5"

PROMPT = """You screen sales leads for a freelancer/agency that sells remote services (software, web, apps, automation,
AI, content writing, design, marketing, data, video, virtual assistance).
Decide if the post below is from someone (or a company) who wants to HIRE or BUY such a service.
Answer ONLY with JSON: {"verdict": "buyer" | "maybe" | "reject", "summary": "<max 20 words: what they need>"}
- "buyer": clearly wants to hire/buy remote service work.
- "reject": a seller/freelancer advertising, job seeker, showcase, tutorial, discussion, spam, or on-site only.
- "maybe": unclear.

Title: {title}
Platform: {platform}
Text: {text}"""

_JSON = re.compile(r"\{.*\}", re.S)


def parse_verdict(raw: str) -> tuple[str, str]:
    m = _JSON.search(raw or "")
    if not m:
        return "", ""
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return "", ""
    v = str(d.get("verdict", "")).lower().strip()
    if v not in ("buyer", "maybe", "reject"):
        return "", ""
    return v, str(d.get("summary", "")).strip()


def review_one(lead: dict, key: str, model: str, session: requests.Session | None = None) -> tuple[str, str]:
    s = session or requests
    prompt = (PROMPT.replace("{title}", lead["title"][:300]).replace("{platform}", lead["platform"])
              .replace("{text}", (lead["text"] or "")[:1800]))
    r = s.post(API, timeout=45, headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                                          "content-type": "application/json"},
               json={"model": model, "max_tokens": 150, "messages": [{"role": "user", "content": prompt}]})
    if r.status_code != 200:
        raise RuntimeError(f"anthropic {r.status_code}: {r.text[:200]}")
    blocks = r.json().get("content", [])
    return parse_verdict("".join(b.get("text", "") for b in blocks))


def review(cfg: dict, store: Store, limit: int | None = None, progress=None) -> dict:
    acfg = cfg.get("ai", {}) or {}
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key or acfg.get("enabled") is False:
        return {"reviewed": 0, "rejected": 0, "confirmed": 0, "skipped": "no ANTHROPIC_API_KEY"}
    lo, hi = acfg.get("band", [35, 75])
    limit = limit or int(acfg.get("max_per_run", 60))
    model = acfg.get("model", DEFAULT_MODEL)
    sess = requests.Session()
    done = rej = conf = 0
    err = ""
    for lead in store.for_ai_review(limit, lo, hi):
        try:
            verdict, summary = review_one(lead, key, model, sess)
        except Exception as e:          # network / quota: stop quietly, keep what we have
            err = str(e)
            log.warning("ai review stopped: %s", err)
            break
        if not verdict:
            continue
        if verdict == "reject":
            store.set_ai(lead["id"], verdict, summary)
            store.update_user_fields(lead["id"], status="ignored")
            rej += 1
        else:
            store.set_ai(lead["id"], verdict, summary, 5 if verdict == "buyer" else 0)
            conf += verdict == "buyer"
        done += 1
        if progress and done % 10 == 0:
            progress(f"ai: reviewed {done}")
    return {"reviewed": done, "rejected": rej, "confirmed": conf, "error": err}
