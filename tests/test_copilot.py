import json

import requests

from leabee.copilot import client, spec, tools
from leabee.storage import Store
from leabee.web import create_app


def _lead(i, score=70, **kw):
    d = dict(id=i * 10, source="reddit", platform="Reddit", lead_type="buyer", title=f"Need {i} automation developer", text="body text", url=f"https://u/{i}",
             author="", author_url="", posted_at="2099-01-01T00:00:00+00:00", categories="automation", country="India", budget_text="$500", budget_usd=500,
             contact="", score=score, reasons="+30 intent: x | +20 fit: y", enriched=0, language="en", grade="B", kind="gig", next_action="Reply in the thread",
             details=json.dumps({"deadline": "Nov 20"}))
    d.update(kw)
    return d


def test_tools_search_details_draft_status_summary(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("LEABEE_LLM_BASE_URL", raising=False)
    s = Store(":memory:")
    s.upsert(_lead("a", 90, grade="A")); s.upsert(_lead("b", 55, grade="C")); s.upsert(_lead("c", 30, grade="D", title="low one"))
    r = tools.search_leads(s, category="automation", min_grade="B")
    assert r["total_matching"] == 1 and r["leads"][0]["grade"] == "A" and len(r["leads"][0]["id"]) <= 20
    assert tools.search_leads(s, min_grade="C")["total_matching"] == 2 and tools.search_leads(s, kind="prospect")["total_matching"] == 0
    lid = r["leads"][0]["id"]
    d = tools.lead_details(s, lid)
    assert d["why"] == ["+30 intent: x", "+20 fit: y"] and d["details"]["deadline"] == "Nov 20" and tools.lead_details(s, "zzz")["error"]
    dr = tools.draft_reply(s, {"profile": {"name": "Sam"}}, lid)
    assert dr["written_by"] == "template" and "Sam" in dr["short_message"] and "Draft only" in dr["reminder"]
    assert tools.set_lead_status(s, lid, "bogus")["error"] and tools.set_lead_status(s, lid, "contacted", 3)["follow_up"]
    assert s.find_by_prefix(lid)["status"] == "contacted"
    summ = tools.pipeline_summary(s)
    assert summ["active_leads"] == 3 and summ["by_status"]["contacted"] == 1


def test_policies_keep_the_copilot_safe_and_every_tool_is_covered():
    text = " ".join(c for _, c, _ in spec.POLICIES).lower()
    assert "never claim a message was sent" in text and "explicit yes" in text and "never invent" in text
    used = {t for _, _, names in spec.POLICIES for t in names}
    assert used == {"search_leads", "lead_details", "draft_reply", "set_lead_status", "pipeline_summary", "find_businesses"}


def test_client_flow_and_errors(monkeypatch):
    calls = []

    class R:
        def __init__(self, code, body): self.status_code, self.body = code, body
        def json(self): return self.body
        def raise_for_status(self):
            if self.status_code >= 400: raise requests.HTTPError()

    def post(url, json=None, timeout=0, **k):
        calls.append(("POST", url, json))
        return R(200, {"id": "sess1"}) if url.endswith("/sessions") else R(200, {"offset": 4})

    def get(url, params=None, timeout=0, **k):
        calls.append(("GET", url, params))
        return R(200, [{"data": {"message": "Here are 2 leads."}}])
    monkeypatch.setattr(client.requests, "post", post)
    monkeypatch.setattr(client.requests, "get", get)
    out = client.ask("best leads?")
    assert out == {"session_id": "sess1", "reply": "Here are 2 leads.", "pending": False}
    assert calls[0][2]["agent_id"] == spec.AGENT_ID and calls[1][2] == {"kind": "message", "source": "customer", "message": "best leads?"}
    assert calls[2][2] == {"min_offset": 5, "source": "ai_agent", "kinds": "message", "wait_for_data": 55}
    monkeypatch.setattr(client.requests, "get", lambda *a, **k: R(504, {}))
    assert client.ask("hi", "sess1")["pending"] is True
    def refuse(*a, **k): raise requests.ConnectionError()
    monkeypatch.setattr(client.requests, "post", refuse)
    assert "python -m leabee copilot" in client.ask("hi")["error"]


def test_dashboard_proxy(monkeypatch):
    app = create_app({"min_score": 35, "max_age_days": 99999, "hot_score": 65}, Store(":memory:")).test_client()
    assert app.post("/api/copilot/ask", json={}).status_code == 400
    monkeypatch.setattr("leabee.copilot.client.ask", lambda m, sid="": {"session_id": "s", "reply": "ok " + m, "pending": False})
    r = app.post("/api/copilot/ask", json={"message": "hello"})
    assert r.status_code == 200 and r.get_json()["reply"] == "ok hello"
    monkeypatch.setattr("leabee.copilot.client.ask", lambda m, sid="": {"error": "down"})
    assert app.post("/api/copilot/ask", json={"message": "x"}).status_code == 503
    assert app.get("/api/tools").status_code == 200 and "Copilot" in app.get("/").get_data(as_text=True)
