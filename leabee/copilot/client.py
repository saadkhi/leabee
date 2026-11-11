"""Small client for Parlant's REST API (documented in Parlant docs/sessions.md). Used by the dashboard chat panel."""
from __future__ import annotations

import os

import requests

from .spec import AGENT_ID


def base_url() -> str:
    return os.environ.get("PARLANT_URL", "http://localhost:8800").rstrip("/")


def ask(message: str, session_id: str = "", wait_s: int = 55) -> dict:
    """Send one customer message and wait for the agent's reply. Returns {session_id, reply, pending}."""
    url = base_url()
    try:
        if not session_id:
            r = requests.post(f"{url}/sessions", json={"agent_id": AGENT_ID, "title": "Leabee dashboard"}, timeout=20)
            r.raise_for_status()
            session_id = r.json()["id"]
        r = requests.post(f"{url}/sessions/{session_id}/events", json={"kind": "message", "source": "customer", "message": message[:2000]}, timeout=20)
        r.raise_for_status()
        offset = int(r.json().get("offset", 0)) + 1
        r = requests.get(f"{url}/sessions/{session_id}/events", timeout=wait_s + 10,
                         params={"min_offset": offset, "source": "ai_agent", "kinds": "message", "wait_for_data": wait_s})
    except requests.ConnectionError:
        return {"error": "The copilot server is not running. Start it with: python -m leabee copilot", "session_id": session_id}
    except requests.RequestException as e:
        return {"error": f"copilot request failed: {e.__class__.__name__}", "session_id": session_id}
    if r.status_code == 504:
        return {"session_id": session_id, "reply": "", "pending": True}
    if r.status_code != 200:
        return {"error": f"copilot returned HTTP {r.status_code}", "session_id": session_id}
    events = r.json()
    events = events if isinstance(events, list) else events.get("events", [])
    texts = [(e.get("data") or {}).get("message", "") for e in events]
    return {"session_id": session_id, "reply": "\n\n".join(t for t in texts if t), "pending": False}
