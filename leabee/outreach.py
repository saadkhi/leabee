"""Reply drafts. You review and send manually; nothing is ever sent automatically.

With ANTHROPIC_API_KEY set, drafts are written by Claude from the lead's own words, details and your `profile:`
(name, pitch, portfolio link, rate). Without a key (or on any error) the category templates below are used."""
from __future__ import annotations

import json
import os
import re

import requests

CATEGORY_PITCH = {
    "web_development": "build and launch websites and web apps quickly",
    "mobile_apps": "design and build iOS/Android apps",
    "custom_software": "build custom software and internal tools for small businesses",
    "automation": "automate repetitive business processes (Zapier / Make / n8n / custom scripts)",
    "ai_ml": "build AI features, chatbots and agents that save time",
    "content_writing": "write clear, SEO-friendly content and copy",
    "design": "create clean UI/UX and brand design",
    "marketing_seo": "grow traffic and leads with SEO and marketing",
    "data": "collect, clean and analyse data (scrapers, dashboards, reports)",
    "video_audio": "edit and produce video and audio content",
    "virtual_assistant": "handle admin, inbox and operations as a reliable virtual assistant",
    "devops_cloud": "set up and maintain cloud infrastructure and CI/CD",
    "game_dev": "build games and interactive experiences",
}
API = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-5-5"


def _name(cfg: dict | None = None) -> str:
    return ((cfg or {}).get("profile") or {}).get("name") or os.environ.get("LEABEE_YOUR_NAME", "").strip() or "[Your name]"


def template_draft(lead: dict, cfg: dict | None = None) -> dict:
    cats = [c for c in (lead.get("categories") or "").split(",") if c]
    pitch = next((CATEGORY_PITCH[c] for c in cats if c in CATEGORY_PITCH), "help with this kind of project")
    title = (lead.get("title") or "").strip()
    ref = title if len(title) <= 90 else title[:87] + "..."
    me = _name(cfg)
    if lead.get("kind") == "prospect":
        try:
            d = json.loads(lead.get("details") or "{}")
        except ValueError:
            d = {}
        gap = (d.get("gaps") or ["your online presence"])[0]
        biz = d.get("company") or lead.get("author") or "your business"
        short = (f"Hi! I came across {biz} on Google Maps and noticed {gap}. I help local businesses with {d.get('pitch', 'their online presence')}. "
                 f"Happy to send a quick idea or two, no obligation. If it's not relevant, just say so. — {me}")
        long = (f"Hi,\n\nI was looking at {biz} and noticed {gap}. I {pitch if cats else 'help local businesses get more customers online'}, "
                f"and thought a short, specific suggestion might be useful.\n\nIf you'd like, I can send 2-3 concrete ideas for {biz} (free, no strings).\n\n"
                f"Best,\n{me}\n\nP.S. If this isn't relevant, tell me and I won't follow up.")
        return {"short": short, "long": long, "follow_up": f"Hi, following up on my note about {biz}. Happy to share the ideas whenever it suits. — {me}", "source": "template"}
    short = (f"Hi! Saw your post (\"{ref}\"). I {pitch} and can start this week. Happy to share relevant examples. "
             f"Want a quick call or a short brief first? — {me}")
    long = (f"Hi,\n\nI came across your post \"{ref}\" on {lead.get('platform', 'the platform')}.\n\nI {pitch}. For a project like this I would: "
            f"(1) confirm scope and success criteria, (2) share a clear timeline and fixed or hourly quote, (3) deliver in small milestones so you see progress early.\n\n"
            f"If you can share a bit more detail (goals, deadline, budget range), I can reply with a concrete plan today.\n\nBest,\n{me}\n\n"
            f"P.S. If this isn't relevant, just say so and I won't follow up.")
    follow = f"Hi, just following up on my earlier message about \"{ref}\". Still happy to help if this is open, otherwise no worries. — {me}"
    return {"short": short, "long": long, "follow_up": follow, "source": "template"}


def ai_draft(lead: dict, cfg: dict | None = None, key: str = "", timeout: float = 60) -> dict | None:
    key = key or os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return None
    prof = (cfg or {}).get("profile") or {}
    try:
        details = json.loads(lead.get("details") or "{}")
    except ValueError:
        details = {}
    prompt = (
        "You write replies for a freelancer/agency answering a sales lead. Write in the SAME language as the lead. Be specific to their post, "
        "concrete, humble, 60-110 words for 'short' (a DM/comment), 120-180 for 'long' (an email), 35-60 for 'follow_up'. "
        "Never invent experience, prices, clients or links. Use only the seller facts given. Do not use hype. Ask at most one question. "
        "End 'long' with a one-line opt-out. Return ONLY JSON: {\"short\": \"...\", \"long\": \"...\", \"follow_up\": \"...\"}\n\n"
        f"SELLER: name={_name(cfg)}; services={', '.join(prof.get('services') or []) or 'not specified'}; skills={', '.join(prof.get('skills') or []) or 'not specified'}; "
        f"pitch={prof.get('pitch', '')}; portfolio={prof.get('portfolio_url', '')}; rate={prof.get('rate', '')}\n"
        f"LEAD ({lead.get('kind', 'gig')}, {lead.get('platform')}): title={lead.get('title')}\nDETAILS: {json.dumps(details, ensure_ascii=False)[:600]}\n"
        f"POST: {(lead.get('text') or '')[:1800]}")
    try:
        r = requests.post(API, timeout=timeout, headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                          json={"model": os.environ.get("LEABEE_AGENT_MODEL", MODEL), "max_tokens": 900, "messages": [{"role": "user", "content": prompt}]})
        if r.status_code != 200:
            return None
        text = "".join(b.get("text", "") for b in r.json().get("content", []))
        m = re.search(r"\{.*\}", text, re.S)
        d = json.loads(m.group(0)) if m else None
    except (requests.RequestException, ValueError):
        return None
    if not isinstance(d, dict) or not all(isinstance(d.get(k), str) and d[k].strip() for k in ("short", "long", "follow_up")):
        return None
    return {"short": d["short"].strip(), "long": d["long"].strip(), "follow_up": d["follow_up"].strip(), "source": "ai"}


def draft(lead: dict, cfg: dict | None = None, use_ai: bool = True) -> dict:
    return (ai_draft(lead, cfg) if use_ai else None) or template_draft(lead, cfg)
