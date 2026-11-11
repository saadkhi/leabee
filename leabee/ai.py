"""Optional AI second opinion on borderline leads. Uses the app's LLM client (OpenRouter free models or local Ollama; skipped when none).

Rule-based scoring is fast and free but imperfect. For leads scoring in a middle band, a small model decides
"is this person actually looking to pay for a remote service?" and writes a one-line summary."""
from __future__ import annotations

import json
import logging
import re

from . import llm
from . import security as sec
from .storage import Store

log = logging.getLogger("leabee.ai")

PROMPT = """You screen sales leads for a freelancer/agency that sells remote services (software, web, apps, automation,
AI, content writing, design, marketing, data, video, virtual assistance).
Decide if the post below is from someone (or a company) who wants to HIRE or BUY such a service.
Answer ONLY with JSON: {"verdict": "buyer" | "maybe" | "reject", "summary": "<max 20 words: what they need>"}
- "buyer": clearly wants to hire/buy remote service work.
- "reject": a seller/freelancer advertising, job seeker, employee job ad, showcase, tutorial, discussion, spam, or on-site only.
- "maybe": unclear.

Platform: {platform}
Post:
{post}"""

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


def review_one(lead: dict) -> tuple[str, str]:
    post = sec.untrusted(f"{lead['title'][:300]}\n{lead['text'] or ''}")
    prompt = PROMPT.replace("{platform}", str(lead["platform"])[:40]).replace("{post}", post)
    return parse_verdict(llm.complete(prompt, system=sec.UNTRUSTED_NOTE, max_tokens=150, temperature=0))


def review(cfg: dict, store: Store, limit: int | None = None, progress=None) -> dict:
    acfg = cfg.get("ai", {}) or {}
    if not llm.available() or acfg.get("enabled") is False:
        return {"reviewed": 0, "rejected": 0, "confirmed": 0, "skipped": "no LLM configured (OPENROUTER_API_KEY or LEABEE_LLM_BASE_URL)"}
    lo, hi = acfg.get("band", [35, 75])
    limit = limit or int(acfg.get("max_per_run", 60))
    done = rej = conf = 0
    err = ""
    for lead in store.for_ai_review(limit, lo, hi):
        try:
            verdict, summary = review_one(lead)
        except llm.LLMError as e:          # quota / network: stop quietly, keep what we have
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
