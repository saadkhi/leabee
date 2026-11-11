import json

from leabee import llm, outreach

LEAD = {"title": "Need an n8n expert for Shopify", "text": "We need orders synced to WhatsApp. Budget $500.", "platform": "Reddit", "kind": "gig",
        "categories": "automation", "details": json.dumps({"tech": ["n8n"]})}


def _no_llm(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("LEABEE_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LEABEE_LLM_API_KEY", raising=False)


def test_template_when_no_llm(monkeypatch):
    _no_llm(monkeypatch)
    d = outreach.draft(LEAD, {"profile": {"name": "Sam"}})
    assert d["source"] == "template" and "Sam" in d["short"] and "automate" in d["short"]


def test_ai_draft_parses_and_falls_back(monkeypatch):
    _no_llm(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    reply = {"short": "Hi, n8n to WhatsApp is my thing.", "long": "Long email. Reply STOP to opt out.", "follow_up": "Following up."}
    seen = {}

    def fake_complete(prompt, system="", **kw):
        seen["prompt"] = prompt
        return "Here: " + json.dumps(reply)
    monkeypatch.setattr(llm, "complete", fake_complete)
    d = outreach.draft(LEAD, {"profile": {"name": "Sam", "skills": ["n8n"], "portfolio_url": "https://sam.test"}})
    assert d["source"] == "ai" and d["short"] == reply["short"]
    assert "Never invent" in seen["prompt"] and "https://sam.test" in seen["prompt"] and "Budget $500" in seen["prompt"]
    monkeypatch.setattr(llm, "complete", lambda *a, **k: (_ for _ in ()).throw(llm.LLMError("down")))
    assert outreach.draft(LEAD)["source"] == "template"                       # LLM error -> templates
    monkeypatch.setattr(llm, "complete", lambda *a, **k: '{"short": "only"}')
    assert outreach.draft(LEAD)["source"] == "template"                       # incomplete JSON -> templates


def test_prospect_pitch_mentions_the_gap(monkeypatch):
    _no_llm(monkeypatch)
    lead = {"title": "Joe's Pizza", "kind": "prospect", "author": "Joe's Pizza", "categories": "web_development,marketing_seo",
            "details": json.dumps({"company": "Joe's Pizza", "gaps": ["no website"], "pitch": "a website"})}
    d = outreach.draft(lead, {"profile": {"name": "Sam"}})
    assert "no website" in d["short"] and "Google Maps" in d["short"]
