"""Tool implementations for the copilot. Plain functions over Store, so they are tested without Parlant or an LLM."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from ..outreach import draft
from ..storage import STATUSES, Store

GRADE_MIN = {"A": 80, "B": 65, "C": 50, "D": 0}


def _brief(l: dict) -> dict:
    return {"id": l["id"][:20], "grade": l.get("grade") or "", "score": l["score"], "title": l["title"][:120], "platform": l["platform"],
            "kind": l.get("kind") or "", "country": l.get("country") or "unknown", "budget": l.get("budget_text") or "", "status": l["status"],
            "contact": (l.get("contact") or "")[:80], "next_action": l.get("next_action") or "", "url": l["url"]}


def search_leads(store: Store, query: str = "", category: str = "", country: str = "", min_grade: str = "C", kind: str = "", limit: int = 5) -> dict:
    rows, total = store.query(q=query.strip(), category=category.strip(), country=country.strip(), kind=kind.strip(), status="new",
                              min_score=GRADE_MIN.get(min_grade.upper(), 50), limit=max(1, min(int(limit), 10)))
    return {"total_matching": total, "shown": len(rows), "leads": [_brief(l) for l in rows]}


def lead_details(store: Store, lead_id: str) -> dict:
    l = store.find_by_prefix(lead_id)
    if not l:
        return {"error": "No single lead matches that id."}
    try:
        details = json.loads(l.get("details") or "{}")
    except ValueError:
        details = {}
    return {**_brief(l), "why": [x for x in (l.get("reasons") or "").split(" | ") if x], "details": details,
            "post": (l.get("text") or "")[:1200], "notes": l.get("notes") or ""}


def draft_reply(store: Store, cfg: dict, lead_id: str) -> dict:
    l = store.find_by_prefix(lead_id)
    if not l:
        return {"error": "No single lead matches that id."}
    d = draft(l, cfg)
    return {"lead": l["title"][:100], "written_by": d["source"], "short_message": d["short"], "email": d["long"], "follow_up": d["follow_up"],
            "reminder": "Draft only. The user reviews and sends it themselves."}


def set_lead_status(store: Store, lead_id: str, status: str, follow_up_days: int = 0) -> dict:
    l = store.find_by_prefix(lead_id)
    if not l:
        return {"error": "No single lead matches that id."}
    if status not in STATUSES:
        return {"error": f"status must be one of {', '.join(STATUSES)}"}
    fu = (datetime.now() + timedelta(days=int(follow_up_days))).strftime("%Y-%m-%d") if follow_up_days else None
    store.update_user_fields(l["id"], status=status, follow_up=fu)
    return {"ok": True, "lead": l["title"][:100], "status": status, "follow_up": fu or l.get("follow_up") or ""}


def pipeline_summary(store: Store) -> dict:
    st = store.stats()
    due, _ = store.query(follow_due=True, limit=5, sort="follow_up")
    return {"active_leads": st["total"], "hot": st["hot"], "with_contact": st["with_contact"], "follow_ups_due": st["follow_ups_due"],
            "by_status": {r["status"]: r["n"] for r in st["by_status"]}, "top_categories": st["by_category"][:5],
            "follow_ups": [{"id": l["id"][:20], "title": l["title"][:80], "due": l["follow_up"]} for l in due]}
