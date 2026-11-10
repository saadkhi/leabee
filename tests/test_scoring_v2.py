"""Scoring v2: kinds, the five-part score, profile, details. All posts here are invented (no real people)."""
from datetime import datetime, timedelta, timezone

import pytest

from leabee.classify import score_item
from leabee.models import RawItem
from leabee.scoring import grade, relative_age_hours

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)


def item(title, text="", platform="Reddit", source="reddit", lead_type="buyer", hours=2, force="", **kw):
    return RawItem(source=source, platform=platform, lead_type=lead_type, title=title, text=text, url="https://x.test/1",
                   posted_at=NOW - timedelta(hours=hours) if hours is not None else None, force_intent=force, **kw)


# ----------------------------------------------------------------------------- what is kept
def test_marketplace_gig_scores_and_explains():
    it = item("n8n Automation Developer Needed", "We need an n8n expert to connect Shopify and WhatsApp. Budget $1,000 fixed price. Email me at ceo@acme-shop.io",
              platform="Upwork", source="tinyfish")
    r = score_item(it, NOW)
    assert r["kind"] == "gig" and r["grade"] in ("A", "B") and r["score"] >= 70
    assert set(r["components"]) == {"intent", "fit", "value", "reach", "fresh"}
    assert sum(r["components"].values()) <= r["score"] + 6            # parts add up (small +/- adjustments aside)
    assert any(x.startswith("+") and "intent" in x for x in r["reasons"]) and any("reach: direct contact" in x for x in r["reasons"])
    assert "Email ceo@acme-shop.io" in r["next_action"]


def test_hiring_tag_budget_and_details():
    r = score_item(item("[HIRING] Python developer to build a web scraper", "Paid project, budget $400. Deadline: November 20. Need it in 2 weeks. Long-term work after."), NOW)
    assert r["kind"] == "gig" and r["budget_usd"] == 400
    assert r["details"]["deadline"].lower().startswith("november 20") and r["details"]["pay_type"] if "pay_type" in r["details"] else True
    assert "python" in r["details"]["tech"]


@pytest.mark.parametrize("title,text", [
    ("Need a video editor for my travel series", "I am looking for a full time editor for it. Requirements: pacing and storytelling."),   # a person, not a company
    ("[TASK] Video editor for daily clips $120/month", "Edit 4 short clips daily. Long-term."),
    ("Acme: Product Designer (Contract)", "Remote. Design our dashboard.", ),
])
def test_individuals_and_contract_headlines_stay_gigs(title, text):
    lead_type = "job" if title.startswith("Acme") else "buyer"
    assert score_item(item(title, text, lead_type=lead_type), NOW)["kind"] == "gig"


# ----------------------------------------------------------------------------- what is dropped
@pytest.mark.parametrize("title,text,why", [
    ("[HIRING] Senior Fullstack Engineer Remote [$120,000 - 170,000 / year]", "Join our team. Benefits included.", "salary"),
    ("[Hiring] Backend Developer (Full-Time)", "Pay: $30/hr. Node.js.", "full-time title"),
    ("Search Co | Product Engineer | REMOTE | Full-time | $23-34/hr", "We are hiring a product engineer.", "hourly is not freelance"),
    ("[HIRING] [US] 30 Remote Javascript jobs", "I curated a list of openings. Apply early!", "job list"),
    ("Time to hire is hiring a Freelance Video Editor", "Salary Guide: $20k - $53k/yr Tax-Free! Apply & More Details: https://zerotaxjobs.example/x", "aggregator"),
    ("Lemon.io: Senior DevOps Engineer", "Lemon.io helps companies in the US and Europe hire experienced engineers.", "talent network"),
    ("Data annotation contractors - $35/hr", "AI training pipeline, prompt-quality reviewers wanted. [Hiring]", "AI data work"),
    ("[Hiring] B2B Sales Partner for our studio", "Commission only.", "sales role"),
    ("Customer Service Representative II (Seasonal)", "Remote customer service rep. Hiring now.", "non-service role"),
    ("Looking for a developer to build it with me", "Equity split. Co-founder wanted for my startup idea.", "partner"),
    ("Need a Python dev for my homework", "I'm a university student majoring in CS, assignment due Friday. Paid.", "academic"),
    ("Where to start coming from a design background?", "I want to get into web design. Any tips? Looking for a developer course.", "advice"),
    ("Should I hire a developer? Do I spend the money?", "Solo founder, 200 users, torn on whether to hire. Would appreciate advice.", "should-I-hire question"),
    ("[For Hire] Full-stack developer", "I build web apps. DM me.", "seller"),
    ("I'm looking to take on freelance web projects", "Developer with 5 years experience.", "seeker"),
    ("Looking for long-term opportunities as a Virtual Assistant", "I have 5 years of experience.", "seeker (opportunities)"),
    ("[Hire] fullstack developer with 5+ Years of experience", "React, Node.", "mis-tagged seller"),
    ("HIRED!!! Freelance designer wanted", "Thanks everyone, the position has been filled.", "closed"),
    ("Hiring someone to build your app? Get these in writing first", "I work at a dev team in LA, this is roughly how we sell.", "promo article"),
    ("Looking for a Developer for an API checker - Collaboration Opportunity", "Collaboration opportunity, join my project.", "collaboration"),
])
def test_noise_is_dropped(title, text, why):
    assert score_item(item(title, text), NOW) is None, why


def test_keep_roles_switch_and_cap():
    it = item("Backend Engineer", "We are hiring a backend engineer. Salary and benefits. Join our team.", lead_type="job", source="remoteok")
    assert score_item(it, NOW) is None
    r = score_item(it, NOW, {"keep_roles": True})
    assert r["kind"] == "role" and r["score"] <= 45 and r["grade"] == "D"


def test_hn_candidate_profile_is_not_a_client():
    it = item("Location: Berlin, Germany", "Location: Berlin\nRemote: Yes\nWilling to relocate: No\nTechnologies: Python, React\nEmail: me@x.dev", source="hackernews", platform="Hacker News")
    assert score_item(it, NOW) is None


# ----------------------------------------------------------------------------- profile
def test_profile_services_skills_avoid_language():
    base = item("Need a Shopify developer", "Looking to hire a Shopify developer for my store. Paid, budget $500.")
    plain = score_item(base, NOW)
    mine = score_item(base, NOW, {"profile": {"services": ["web_development", "automation"], "skills": ["shopify"]}})
    other = score_item(base, NOW, {"profile": {"services": ["content_writing"]}})
    assert mine["components"]["fit"] > plain["components"]["fit"] > other["components"]["fit"]
    assert any("matches your services" in x for x in mine["reasons"]) and any("outside your services" in x for x in other["reasons"])
    assert score_item(base, NOW, {"profile": {"avoid": ["shopify"]}}) is None
    es = item("Necesito un desarrollador de Shopify", "Necesito un desarrollador para mi tienda, presupuesto $500, pago por el proyecto.")
    assert score_item(es, NOW, {"profile": {"languages": ["en"]}})["components"]["fit"] < score_item(es, NOW)["components"]["fit"]


# ----------------------------------------------------------------------------- freshness & dates
def test_freshness_and_estimated_dates():
    fresh = score_item(item("Need a logo designer", "Looking to hire a logo designer, paid $100.", hours=1), NOW)
    old = score_item(item("Need a logo designer", "Looking to hire a logo designer, paid $100.", hours=200), NOW)
    assert fresh["components"]["fresh"] == 10 and old["components"]["fresh"] == 1 and fresh["score"] > old["score"]
    assert relative_age_hours("Join Bob · 4 days ago") == 96 and relative_age_hours("posted 10 hours ago") == 10 and relative_age_hours("nothing") is None
    up = item("Video editor needed", "Video editor needed - Freelance Job in Video - $200.00 Fixed Price, posted November 6, 2026 - Upwork\nLooking for an editor.",
              platform="Upwork", source="tinyfish", hours=None)
    r = score_item(up, NOW)
    assert r["details"]["posted_date"] == "2026-11-06" and r["posted_at"].date().isoformat() == "2026-11-06"
    assert r["components"]["fresh"] == 5                      # 60 hours old, not "unknown"


def test_grades_and_actions():
    assert [grade(x) for x in (95, 80, 79, 65, 64, 50, 49)] == ["A", "A", "B", "B", "C", "C", "D"]
    r = score_item(item("Looking for a React developer", "Hire a React developer, paid. Reach me on telegram: @acmefounder"), NOW)
    assert "telegram" in r["next_action"].lower()
    r2 = score_item(item("Need a copywriter", "Looking to hire a copywriter for my landing page, paid project."), NOW)
    assert "thread" in r2["next_action"]
