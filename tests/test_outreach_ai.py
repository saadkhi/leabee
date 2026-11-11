import json
from types import SimpleNamespace

from leabee import outreach

LEAD = {"title": "Need an n8n expert for Shopify", "text": "We need orders synced to WhatsApp. Budget $500.", "platform": "Reddit", "kind": "gig",
        "categories": "automation", "details": json.dumps({"tech": ["n8n"]})}


def test_template_when_no_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    d = outreach.draft(LEAD, {"profile": {"name": "Sam"}})
    assert d["source"] == "template" and "Sam" in d["short"] and "automate" in d["short"]


def test_ai_draft_parses_and_falls_back(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    seen = {}
    reply = {"short": "Hi, n8n to WhatsApp is my thing.", "long": "Long email. Reply STOP to opt out.", "follow_up": "Following up."}

    class R:
        status_code = 200
        def json(self): return {"content": [{"type": "text", "text": "Here: " + json.dumps(reply)}]}
    monkeypatch.setattr(outreach.requests, "post", lambda url, **kw: seen.update(kw) or R())
    d = outreach.draft(LEAD, {"profile": {"name": "Sam", "skills": ["n8n"], "portfolio_url": "https://sam.test"}})
    assert d["source"] == "ai" and d["short"] == reply["short"]
    body = seen["json"]["messages"][0]["content"]
    assert "Never invent" in body and "https://sam.test" in body and "Budget $500" in body and seen["headers"]["x-api-key"] == "k"
    monkeypatch.setattr(outreach.requests, "post", lambda *a, **k: SimpleNamespace(status_code=500, json=lambda: {}))
    assert outreach.draft(LEAD)["source"] == "template"                       # API error -> templates
    bad = SimpleNamespace(status_code=200, json=lambda: {"content": [{"text": "{\"short\": \"only\"}"}]})
    monkeypatch.setattr(outreach.requests, "post", lambda *a, **k: bad)
    assert outreach.draft(LEAD)["source"] == "template"                       # incomplete JSON -> templates


def test_prospect_pitch_mentions_the_gap(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    lead = {"title": "Joe's Pizza", "kind": "prospect", "author": "Joe's Pizza", "categories": "web_development,marketing_seo",
            "details": json.dumps({"company": "Joe's Pizza", "gaps": ["no website"], "pitch": "a website"})}
    d = outreach.draft(lead, {"profile": {"name": "Sam"}})
    assert "no website" in d["short"] and "Google Maps" in d["short"] and "opt" not in d["short"].lower() or "just say so" in d["short"]
