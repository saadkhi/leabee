from datetime import datetime, timedelta, timezone

import pytest

from leabee.classify import (detect_categories, detect_country, extract_budget, extract_contacts,
                             score_item)
from leabee.models import RawItem

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)


def item(title, text="", force="", hours=2, lead_type="buyer", **kw):
    return RawItem(source="t", platform="T", lead_type=lead_type, title=title, text=text, url="https://x.test/1",
                   posted_at=NOW - timedelta(hours=hours) if hours is not None else None, force_intent=force, **kw)


# ---- buyers that MUST be kept -------------------------------------------------
@pytest.mark.parametrize("title,text", [
    ("Looking for a freelance web developer for my Shopify store", "Budget is $500. DM me."),
    ("Need someone to build an MVP for my SaaS", "We have a spec, willing to pay $3000."),
    ("[HIRING] Python developer to automate invoice workflow", "Paid project."),
    ("Looking for a content writer for our blog", "5 articles a month, $0.10 per word"),
    ("Can anyone recommend a good developer for a mobile app?", "Flutter app, small budget."),
    ("I need a copywriter for my landing page", "Our startup launches next week."),
    ("Hiring a freelance n8n automation expert", "We want to automate our CRM workflow."),
    ("Who can build a chatbot for my business?", "Willing to pay."),
    ("Need an AI developer for my startup", "GPT-based agent, budget $2k"),
    ("Busco desarrollador web freelance", "Necesito una página web para mi tienda online"),
    ("Suche Entwickler für App", "Wir suchen einen App Entwickler für ein Projekt, remote."),
])
def test_buyers_kept(title, text):
    r = score_item(item(title, text), NOW)
    assert r is not None, title
    assert r["score"] >= 35


# ---- sellers / noise that MUST be dropped --------------------------------------
@pytest.mark.parametrize("title,text", [
    ("[FOR HIRE] Full stack developer available", "I have 8 years of experience. Check out my portfolio."),
    ("I will build your website for $50", "Fast delivery. Hire me!"),
    ("Experienced content writer available for freelance projects", "My services include blog writing and SEO."),
    ("Seeking work: React developer", "Open to new opportunities"),
    ("I'm a freelance web designer looking for clients", "DM me for my rates"),
    ("How do I learn web development?", "Looking for a bootcamp or course."),
    ("Show HN: I built a scraper", "Open source"),
    ("[OFFER] Logo design from $20", "Taking on new clients"),
])
def test_sellers_dropped(title, text):
    assert score_item(item(title, text), NOW) is None, title


def test_forced_seller_dropped_and_forced_buyer_kept():
    assert score_item(item("Anything about website", "web developer", force="seller"), NOW) is None
    assert score_item(item("Website redesign", "need web developer help", force="buyer"), NOW) is not None


def test_no_service_match_dropped():
    assert score_item(item("Looking for a plumber", "Need someone to fix my sink"), NOW) is None


def test_old_posts_dropped():
    r = score_item(item("Looking for a web developer", "need a website", hours=24 * 40), NOW, {"max_age_days": 21})
    assert r is None


def test_fresher_scores_higher():
    a = score_item(item("Looking for a web developer", "need a website built", hours=1), NOW)
    b = score_item(item("Looking for a web developer", "need a website built", hours=24 * 10), NOW)
    assert a["score"] > b["score"]


def test_budget_and_contact_raise_score():
    base = score_item(item("Looking for a web developer", "need a website built"), NOW)
    rich = score_item(item("Looking for a web developer", "need a website built, budget $2,000. email me at a@biz.io"), NOW)
    assert rich["score"] > base["score"]
    assert rich["contact"] == "a@biz.io"


def test_job_opening_contract_beats_fulltime():
    ft = score_item(item("Senior React Developer", "full-time react developer", lead_type="job"), NOW)
    ct = score_item(item("React Developer (contract)", "freelance contract react developer", lead_type="job"), NOW)
    assert ct["score"] > ft["score"]


# ---- helpers ---------------------------------------------------------------------
def test_categories():
    c = detect_categories("need a wordpress site and a copywriter, plus zapier automation")
    assert {"web_development", "content_writing", "automation"} <= set(c)


@pytest.mark.parametrize("text,usd", [
    ("budget is $500", 500), ("paying $1,500 for it", 1500), ("$2k budget", 2000), ("$3k-$5k", 3000),
    ("around 800 USD", 800), ("€1000 total", 1080),
])
def test_budget(text, usd):
    t, v = extract_budget(text)
    assert t and v == pytest.approx(usd, rel=0.01), (text, t, v)


def test_hourly_budget_text_only():
    t, v = extract_budget("$40/hr")
    assert t and v is None


def test_no_budget():
    assert extract_budget("no money talk 2024 version 3") == ("", None)


def test_contacts_filters_junk():
    c = extract_contacts("mail bob@acme.com or noreply@x.com or logo@2x.png; telegram: @bob_dev; "
                         "book https://calendly.com/bob/30min")
    assert "bob@acme.com" in c and "telegram:@bob_dev" in c and any("calendly" in x for x in c)
    assert not any("noreply" in x or x.endswith(".png") for x in c)


@pytest.mark.parametrize("text,country", [
    ("Based in London, UK", "United Kingdom"), ("Our team in Karachi", "Pakistan"),
    ("Dubai startup", "UAE"), ("Berlin based", "Germany"), ("someone from Toronto", "Canada"),
    ("nothing here", ""),
])
def test_country(text, country):
    assert detect_country(text) == country


def test_country_hint_and_worldwide():
    assert detect_country("x", "Worldwide") == "Worldwide"
    assert detect_country("text", "Germany only") == "Germany"


@pytest.mark.parametrize("title,text", [
    ("[Delhi] [For Hire] Content Writer, Scriptwriter | Remote", "Looking for clients. $15/hour"),
    ("[Showoff Saturday] I revived my personal website", "now that I'm looking for full-time work."),
    ("[Hiring] SMT Technician - Brooklyn NY", "On-site role, automation lines, $25/hour"),
    ("Looking for work as a React developer", "I am looking for a full-time job"),
])
def test_more_false_positives_dropped(title, text):
    assert score_item(item(title, text), NOW) is None, title


def test_hiring_tag_beats_nothing():
    assert score_item(item("[HIRING] Video editor wanted", "Remote, $20/hr, YouTube channel"), NOW) is not None


@pytest.mark.parametrize("title,text", [
    ("I need a virtual assistant job", "I have some skills to do that."),
    ("any freelance development job, can pm me :)", "I'm looking for a short-time gig (< 2 months) to do websites"),
    ("Customer Success Manager", "remote customer success manager for SaaS", ),
])
def test_job_seekers_and_non_service_roles_dropped(title, text):
    it = item(title, text, lead_type="job" if "Manager" in title else "buyer")
    assert score_item(it, NOW) is None, title


def test_scraped_job_ads_must_be_remote():
    it = item("Python Developer at Acme", "We are looking for a Python developer, Lafayette LA", lead_type="job")
    it.source = "tinyfish"
    assert score_item(it, NOW) is None
    it2 = item("Python Developer at Acme", "Remote Python developer, contract", lead_type="job")
    it2.source = "tinyfish"
    assert score_item(it2, NOW) is not None


@pytest.mark.parametrize("title,text", [
    ("Cerco content creator e SMM", "Ciao Michela, sono Dominique. Content Creator e Social media manager da 5 anni."),
    ("[21F] Need Income | Looking for Remote Content Writing & ...", "I'm a final-year college student looking for ways to earn an income."),
    ("Hi everyone! I'm currently looking for a new opportunity ...", "I'm currently looking for a new opportunity as a WordPress Developer"),
    ("Trabajo remoto programador", "estoy buscando empleo o algún trabajo como programador para ayudarme"),
    ("¡Busco Proyectos!", "Soy desarrollador Full Stack, especializado en el stack MERN"),
    ("LOOKING FOR A DEVELOPER?", "I can help you build: - Websites & Business Websites - Mobile Applications"),
    ("Hi am WordPress Developer. Anyone Hiring?", "We are looking for a Wordpress developer to join our team"),
    ("How to create samples and practice copywriting for first ...", "genuine question, I'm a copywriting beginner"),
])
def test_social_false_positives_dropped(title, text):
    assert score_item(item(title, text), NOW) is None, title


def test_local_employment_ad_dropped_for_tinyfish_only():
    it = item("Web developer job in San Juan", "Sealy is looking for a Web Developer to join our growing team. Full-time, benefits.")
    it.source = "tinyfish"
    assert score_item(it, NOW) is None
    it.source = "reddit"
    assert score_item(it, NOW) is not None


def test_question_without_fix_still_buyer():
    assert score_item(item("Can anyone recommend a good developer for a mobile app?", "Need Flutter app built, budget $3k"), NOW)


@pytest.mark.parametrize("title", [
    "Mindrift: Licensed Physician (MD/DO) - Freelance AI Trainer",
    "Feedzai: Risk&AI Manager",
    "National Claims Assistance Inc: Customer Service Claims Representative",
])
def test_non_service_jobs_dropped(title):
    assert score_item(item(title, "Remote role, freelance contract", lead_type="job"), NOW) is None, title


@pytest.mark.parametrize("title", ["OpenTrain AI: Full-Stack Engineer", "Acme: Senior Python Developer", "Foo: Copywriter (Remote)"])
def test_service_jobs_kept(title):
    assert score_item(item(title, "Remote role, contract", lead_type="job"), NOW) is not None, title


@pytest.mark.parametrize("text,usd", [("$5-10k", 5000), ("5-10k USD", 5000), ("$500-$800", 500), ("$2k-$3k", 2000)])
def test_budget_ranges(text, usd):
    t, v = extract_budget(text)
    assert v == pytest.approx(usd), (text, t, v)
