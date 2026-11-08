"""Draft reply templates. You review and send manually; nothing is ever sent automatically."""
from __future__ import annotations

import os

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


def _name() -> str:
    return os.environ.get("LEABEE_YOUR_NAME", "").strip() or "[Your name]"


def draft(lead: dict) -> dict:
    cats = [c for c in (lead.get("categories") or "").split(",") if c]
    pitch = next((CATEGORY_PITCH[c] for c in cats if c in CATEGORY_PITCH), "help with this kind of project")
    title = (lead.get("title") or "").strip()
    ref = title if len(title) <= 90 else title[:87] + "..."
    short = (f"Hi! Saw your post (\"{ref}\"). I {pitch} and can start this week. "
             f"Happy to share relevant examples. Want a quick call or a short brief first? — {_name()}")
    long = (f"Hi,\n\nI came across your post \"{ref}\" on {lead.get('platform', 'the platform')}.\n\n"
            f"I {pitch}. For a project like this I would: (1) confirm scope and success criteria, "
            f"(2) share a clear timeline and fixed or hourly quote, (3) deliver in small milestones so you see progress early.\n\n"
            f"If you can share a bit more detail (goals, deadline, budget range), I can reply with a concrete plan today.\n\n"
            f"Best,\n{_name()}\n\nP.S. If this isn't relevant, just say so and I won't follow up.")
    follow = (f"Hi, just following up on my earlier message about \"{ref}\". "
              f"Still happy to help if this is open — otherwise no worries. — {_name()}")
    return {"short": short, "long": long, "follow_up": follow}
