"""Extractors, Google Maps prospects and site enrichment. All data is invented."""
import json
import sys
import types
from datetime import datetime, timezone

import pytest

from leabee import extractors as X
from leabee.collectors import gmaps
from leabee.details import extract_company, extract_details, find_website
from leabee.extractors import agents, base
from leabee.models import RawItem
from leabee.pipeline import enrich_sites, item_to_lead
from leabee.prospects import normalise, read_export, record_to_item, score_prospect
from leabee.storage import Store

NOW = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
HTML = """<html><head><title>Acme Dental &amp; Care</title></head><body>
<a href="mailto:info@acmedental.test">email</a> <a href="mailto:dr.jane@acmedental.test?subject=hi">Dr Jane</a>
<a href="tel:+1 (555) 010-2030">call</a> <a href="/contact-us">Contact</a> <a href="/blog">Blog</a>
<a href="https://www.linkedin.com/company/acme-dental">in</a> <a href="https://twitter.com/intent/tweet?x=1">share</a>
<a href="https://instagram.com/acmedental">ig</a>
<script>var e="hidden@script.test"</script><p>Open 9-5. Founded 2026-01-05. Call 555-123-4567 or +44 20 7946 0958.</p></body></html>"""


# ---------------------------------------------------------------------------- details
def test_details_fields_and_company():
    d = extract_details("Acme Corp: Copywriter (Contract)", "Fixed price $500. Less than 30 hrs/week. 1 to 3 months. Need it by Nov 20. "
                        "Native English speaker, 3+ years experience. 20 articles. US Pacific hours. Site: https://acme-co.test/about posted November 3, 2026", "Upwork")
    assert d["company"] == "Acme Corp" and d["website"] == "https://acme-co.test" and d["pay_type"] == "fixed"
    assert "30 hrs" in d["hours"] and "1 to 3 months" in d["duration"] and d["deadline"].lower().startswith("nov 20")
    assert d["language_required"].startswith("native") and d["volume"] == "20 articles" and d["posted_date"] == "2026-11-03"
    assert extract_company("Titre : cherche développeur") == "" and extract_company("Acme | Developer | Remote") == "Acme"
    assert find_website("see https://www.reddit.com/x and https://github.com/x and https://my-biz.test/p") == "https://my-biz.test"


# ---------------------------------------------------------------------------- html parsing & safety
def test_parse_html_contacts_and_socials():
    p = base.parse_html("https://acmedental.test/", HTML)
    assert p.ok and p.title == "Acme Dental & Care"
    assert p.emails[0] == "dr.jane@acmedental.test" and "info@acmedental.test" in p.emails and "hidden@script.test" not in p.emails   # named first, no script text
    assert p.phones[0] == "+15550102030" and any("555-123-4567" in x for x in p.phones)
    assert p.socials == {"linkedin": "https://www.linkedin.com/company/acme-dental", "instagram": "https://instagram.com/acmedental"}   # share links ignored
    assert "https://acmedental.test/contact-us" in p.links


def test_find_phones_ignores_dates_and_ids():
    assert base.find_phones("Founded 2026-01-05, order 1234567890123, call +44 20 7946 0958") == ["+44 20 7946 0958"]


@pytest.mark.parametrize("ip,ok", [("93.184.216.34", True), ("127.0.0.1", False), ("10.0.0.5", False), ("169.254.169.254", False), ("192.168.1.9", False)])
def test_ssrf_guard(monkeypatch, ip, ok):
    monkeypatch.setattr(base.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", (ip, 80))])
    assert base.is_public_url("http://some-host.test/x") is ok
    assert base.is_public_url("ftp://example.com") is False and base.is_public_url("http://localhost/x") is False and base.is_public_url("file:///etc/passwd") is False


def test_plain_backend_blocks_internal_redirect(monkeypatch):
    calls = {"n": 0}

    def fake_public(u):
        return "internal" not in u

    class R:
        status_code, text, headers, url = 200, HTML, {"Content-Type": "text/html"}, "http://internal.test/"
    monkeypatch.setattr("leabee.extractors.plain.is_public_url", fake_public)
    monkeypatch.setattr("leabee.extractors.plain.requests.get", lambda *a, **k: R())
    res = X.PlainBackend().fetch("https://ok.test/")
    assert not res.ok and "redirected" in res.error


# ---------------------------------------------------------------------------- backend selection and site enrichment
class FakeBackend:
    def __init__(self, name, pages, ready=True):
        self.name, self.pages, self.ready, self.calls = name, pages, ready, []

    def available(self):
        return self.ready, "" if self.ready else "off"

    def fetch(self, url, timeout=0):
        self.calls.append(url)
        return self.pages.get(url) or base.PageResult(url=url, backend=self.name, error="404")


def _page(url, html, backend):
    p = base.parse_html(url, html)
    p.backend = backend
    return p


def test_fetch_page_falls_back_to_the_browser_backend(monkeypatch):
    plain = FakeBackend("plain", {})                                              # JS site: plain finds nothing
    js = FakeBackend("crawl4ai", {"https://js.test/": _page("https://js.test/", "<p>" + "Hello from a JS rendered page. " * 10 + "</p>", "crawl4ai")})
    monkeypatch.setitem(X.PAGE_BACKENDS, "plain", lambda: plain)
    monkeypatch.setitem(X.PAGE_BACKENDS, "crawl4ai", lambda: js)
    r = X.fetch_page("https://js.test/", ("plain", "crawl4ai"))
    assert r.ok and r.backend == "crawl4ai" and plain.calls == ["https://js.test/"]
    off = FakeBackend("crawl4ai", {}, ready=False)
    monkeypatch.setitem(X.PAGE_BACKENDS, "crawl4ai", lambda: off)
    assert off.calls == [] and not X.fetch_page("https://js.test/", ("plain", "crawl4ai")).ok                      # unavailable backends are skipped


def test_enrich_site_crawls_contact_page_and_verifies(monkeypatch):
    home = _page("https://acmedental.test/", "<p>" + "Welcome to Acme Dental. " * 12 + '</p><a href="/contact-us">Contact</a><a href="https://linkedin.com/company/acme">x</a>', "plain")
    contact = _page("https://acmedental.test/contact-us", "<p>" + "Reach us any time. " * 12 + '</p><a href="mailto:info@acmedental.test">m</a><a href="mailto:dr.jane@acmedental.test">j</a>', "plain")
    be = FakeBackend("plain", {"https://acmedental.test/": home, "https://acmedental.test/contact-us": contact})
    monkeypatch.setitem(X.PAGE_BACKENDS, "plain", lambda: be)
    monkeypatch.setattr(X, "email_domain_resolves", lambda e: not e.endswith("dead.test"))
    info = X.enrich_site("https://acmedental.test/", {"extractors": {"page_order": ["plain"]}})
    assert info["ok"] and len(info["pages"]) == 2 and info["best_email"] == "dr.jane@acmedental.test"          # named mailbox beats info@
    assert {e["email"]: e["generic"] for e in info["emails"]} == {"dr.jane@acmedental.test": False, "info@acmedental.test": True}
    assert info["socials"]["linkedin"].endswith("/acme")


def test_enrich_site_uses_agent_only_when_asked_and_keyed(monkeypatch):
    empty = _page("https://quiet.test/", "<p>" + "A quiet page without any contact details at all. " * 6 + "</p>", "plain")
    monkeypatch.setitem(X.PAGE_BACKENDS, "plain", lambda: FakeBackend("plain", {"https://quiet.test/": empty}))

    class FakeAgent:
        name = "agentql"
        def available(self): return True, ""
        def extract(self, url, goal, fields): return {"emails": ["owner@quiet.test"], "phones": ["+1 555 010 9999"], "owner_or_contact_name": "Sam Owner", "linkedin": "https://linkedin.com/in/sam"}
    monkeypatch.setitem(X.AGENT_BACKENDS, "agentql", FakeAgent)
    monkeypatch.setattr(X, "email_domain_resolves", lambda e: True)
    cfg = {"extractors": {"page_order": ["plain"]}}
    assert X.enrich_site("https://quiet.test/", cfg, use_agents=False)["best_email"] == ""                       # agents are opt-in
    info = X.enrich_site("https://quiet.test/", cfg, use_agents=True)
    assert info["best_email"] == "owner@quiet.test" and info["owner_or_contact_name"] == "Sam Owner" and info["backend"].endswith("+agentql")


def test_tools_status_lists_all_backends():
    names = {t["name"] for t in X.status()}
    assert names == {"plain", "crawl4ai", "tinyfish", "agentql", "browser_use", "skyvern", "stagehand"}
    assert all(t["ready"] or t["how_to_enable"] for t in X.status())


# ---------------------------------------------------------------------------- agent adapters (fake SDKs: no keys needed)
def test_json_from_llm_text():
    assert agents._json_from('Sure!\n```json\n{"emails": ["a@b.co"]}\n```') == {"emails": ["a@b.co"]}
    assert agents._json_from("no json here") is None and agents._json_from('{"x": 1} trailing') == {"x": 1}


def test_agentql_rest_adapter(monkeypatch):
    monkeypatch.setenv("AGENTQL_API_KEY", "k")
    seen = {}

    class Resp:
        status_code = 200
        def json(self): return {"data": {"emails": ["x@y.co"]}}
    monkeypatch.setattr(agents.requests, "post", lambda url, **kw: seen.update(url=url, **kw) or Resp())
    monkeypatch.setattr(agents, "is_public_url", lambda u: True)
    out = agents.AgentQLBackend().extract("https://y.co", "find contacts", agents.CONTACT_FIELDS)
    assert out == {"emails": ["x@y.co"]} and seen["headers"]["X-API-Key"] == "k" and seen["json"]["url"] == "https://y.co"
    assert "emails[]" in seen["json"]["query"] and "owner_or_contact_name" in seen["json"]["query"]
    monkeypatch.delenv("AGENTQL_API_KEY")
    assert agents.AgentQLBackend().available()[0] is False


def test_browser_use_skyvern_stagehand_adapters_with_fake_sdks(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("SKYVERN_API_KEY", "k")
    monkeypatch.setattr(agents, "is_public_url", lambda u: True)
    reply = {"emails": ["boss@z.co"], "phones": []}

    bu = types.ModuleType("browser_use")
    class Result:
        def final_result(self): return "Here you go: " + json.dumps(reply)
    class Agent:
        def __init__(self, task, **kw): self.task, self.kw = task, kw; bu.last = self
        async def run(self): return Result()
    bu.Agent, bu.Browser, bu.ChatAnthropic = Agent, lambda *a, **k: "browser", lambda m: f"llm:{m}"
    monkeypatch.setitem(sys.modules, "browser_use", bu)
    assert agents.BrowserUseBackend().available()[0]
    assert agents.BrowserUseBackend().extract("https://z.co", "find contacts", agents.CONTACT_FIELDS) == reply
    assert "Do not log in" in bu.last.task and bu.last.kw["llm"].startswith("llm:")

    sk = types.ModuleType("skyvern")
    class Skyvern:
        def __init__(self, api_key=None): assert api_key == "k"
        async def run_task(self, prompt, url, data_extraction_schema): return types.SimpleNamespace(output=reply)
    sk.Skyvern = Skyvern
    monkeypatch.setitem(sys.modules, "skyvern", sk)
    assert agents.SkyvernBackend().extract("https://z.co", "find contacts", agents.CONTACT_FIELDS) == reply

    sh = types.ModuleType("stagehand")
    class Stagehand:
        @classmethod
        async def create(cls, browser, model, model_api_key): sh.model = model; return cls()
        async def extract(self, instruction, schema): return types.SimpleNamespace(data=schema(emails=["boss@z.co"]))
        async def close(self): pass
    class Page:
        async def goto(self, url): pass
    class Ctx:
        async def pages(self): return [Page()]
    class Browser:
        context = Ctx()
        async def close(self): pass
    class LB:
        @staticmethod
        async def launch(headless=True): return Browser()
    sh.Stagehand, sh.local_browser = Stagehand, LB
    monkeypatch.setitem(sys.modules, "stagehand", sh)
    out = agents.StagehandBackend().extract("https://z.co", "find contacts", agents.CONTACT_FIELDS)
    assert out["emails"] == ["boss@z.co"] and sh.model.startswith("anthropic/")


def test_agents_refuse_private_urls(monkeypatch):
    monkeypatch.setenv("AGENTQL_API_KEY", "k")
    called = []
    monkeypatch.setattr(agents.requests, "post", lambda *a, **k: called.append(1))
    assert agents.AgentQLBackend().extract("http://169.254.169.254/", "x", {"a": "b"}) == {} and not called


# ---------------------------------------------------------------------------- Google Maps prospects
API_ROW = {"name": "Joe's Pizza", "main_category": "Pizza restaurant", "website": "", "phone": "+1 512 555 0101", "rating": 3.6, "reviews": 12,
           "address": "12 Main St, Austin, TX", "link": "https://www.google.com/maps/place/joes", "kgmid": "/g/1abc", "can_claim": True,
           "detailed_address": {"country": "United States"}}
CSV_ROW = {"Name": "Bright Smiles Dental", "Main Category": "Dentist", "Website": "https://brightsmiles.test", "Phone": "+971 4 555 0100",
           "Rating": "4.8", "Reviews": "310", "Address": "Dubai Marina, Dubai, UAE", "Emails": "hello@brightsmiles.test;owner@brightsmiles.test",
           "Linkedin": "https://linkedin.com/company/bright"}


def test_normalise_api_and_csv_styles():
    a, c = normalise(API_ROW), normalise(CSV_ROW)
    assert a["category"] == "Pizza restaurant" and a["reviews"] == 12 and a["can_claim"] and a["country"] == "United States"
    assert c["name"] == "Bright Smiles Dental" and c["reviews"] == 310 and c["rating"] == 4.8 and c["emails"] == ["hello@brightsmiles.test", "owner@brightsmiles.test"]


def test_prospect_scoring_need_reach_and_exclusions():
    cfg = {"gmaps": {"target_categories": ["dentist"]}}
    needy = score_prospect(record_to_item(API_ROW), cfg)
    solid = score_prospect(record_to_item(CSV_ROW), cfg)
    assert needy["kind"] == "prospect" and needy["components"]["need"] >= 40 - 5 and "no website" in needy["reasons"][0]
    assert needy["details"]["pitch"] == "a website" and "website" in needy["next_action"]
    assert solid["components"]["reach"] == 15 and solid["components"]["fit"] == 20 and solid["components"]["value"] == 15
    assert needy["components"]["need"] > solid["components"]["need"]
    assert record_to_item({**API_ROW, "name": "McDonald's"}) is not None and score_prospect(record_to_item({**API_ROW, "name": "McDonald's"}), cfg) is None
    assert record_to_item({**API_ROW, "business_status": "Permanently closed"}) is None and record_to_item({"name": ""}) is None


def test_prospect_pipeline_and_storage():
    lead = item_to_lead(record_to_item(CSV_ROW), {"min_score": 35, "gmaps": {}}, NOW)
    assert lead["kind"] == "prospect" and lead["platform"] == "Google Maps" and lead["lead_type"] == "prospect" and "hello@brightsmiles.test" in lead["contact"]
    s = Store(":memory:")
    assert s.upsert(lead) is True and s.get(lead["id"])["grade"] == lead["grade"]
    assert json.loads(s.get(lead["id"])["details"])["company"] == "Bright Smiles Dental"


def test_read_exports_and_collect_from_folder(tmp_path, monkeypatch):
    (tmp_path / "maps").mkdir()
    (tmp_path / "maps" / "a.csv").write_text("Name,Main Category,Website,Phone,Rating,Reviews\nCafe Luna,Cafe,,+1 555 010 1111,4.1,40\n", encoding="utf-8-sig")
    (tmp_path / "maps" / "b.json").write_text(json.dumps({"results": [API_ROW]}), encoding="utf-8")
    assert len(read_export(tmp_path / "maps" / "a.csv")) == 1 and len(read_export(tmp_path / "maps" / "b.json")) == 1
    items = list(gmaps.collect({"database": str(tmp_path / "leads.db"), "gmaps": {}}))
    assert {i.author for i in items} == {"Cafe Luna", "Joe's Pizza"} and all(i.lead_type == "prospect" for i in items)


def test_gmaps_api_mode_creates_polls_and_reads_results(tmp_path, monkeypatch):
    calls = []

    class R:
        def __init__(self, body): self.body, self.status_code = body, 200
        def json(self): return self.body
        def raise_for_status(self): pass
    state = {"polls": 0}

    def post(url, json=None, timeout=0, **k):
        calls.append(("POST", url, json))
        return R({"id": 7}) if url.endswith("create-task-async") else R({"results": [API_ROW], "total_pages": 1})

    def get(url, timeout=0, **k):
        calls.append(("GET", url))
        if url.endswith("/api"):
            return R({})
        state["polls"] += 1
        return R({"status": "in_progress" if state["polls"] < 2 else "completed"})
    monkeypatch.setattr(gmaps.requests, "post", post)
    monkeypatch.setattr(gmaps.requests, "get", get)
    monkeypatch.setattr(gmaps.time, "sleep", lambda s: None)
    cfg = {"database": str(tmp_path / "leads.db"), "gmaps": {"searches": ["pizza in Austin"], "max_results_per_search": 50}}
    items = list(gmaps.collect(cfg))
    assert len(items) == 1
    create = next(c for c in calls if c[0] == "POST" and c[1].endswith("create-task-async"))
    assert create[2]["data"] == {"queries": ["pizza in Austin"], "max_results": 50}                      # {query} placeholder filled
    assert gmaps._template({"a": ["x {query}", {"b": "{query}"}]}, "Q") == {"a": ["x Q", {"b": "Q"}]}


def test_gmaps_api_mode_skips_when_app_not_running(tmp_path, monkeypatch):
    def boom(*a, **k): raise gmaps.requests.ConnectionError("refused")
    monkeypatch.setattr(gmaps.requests, "get", boom)
    cfg = {"database": str(tmp_path / "leads.db"), "gmaps": {"searches": ["x"]}}
    assert list(gmaps.collect(cfg)) == []                                                                   # no crash, just a warning


# ---------------------------------------------------------------------------- site enrichment pipeline
def test_enrich_sites_updates_contact_score_and_does_not_repeat(monkeypatch):
    store = Store(":memory:")
    it = RawItem(source="hackernews", platform="Hacker News", lead_type="buyer", title="Acme | Python contractor | REMOTE | $60/hr",
                 text="We need a Python contractor for our data pipeline, paid project, budget $5000. https://acme-data.test/jobs", url="https://hn.test/1",
                 author="acme", posted_at=NOW)
    lead = item_to_lead(it, {"min_score": 35}, NOW)
    assert lead and json.loads(lead["details"])["website"] == "https://acme-data.test"
    store.upsert(lead)
    before = store.get(lead["id"])
    calls = []

    def fake_enrich(url, cfg=None, use_agents=False):
        calls.append(url)
        return {"ok": True, "backend": "plain", "title": "Acme", "best_email": "cto@acme-data.test", "phones": ["+1 555 010 7777"],
                "socials": {"linkedin": "https://linkedin.com/company/acme"}, "pages": [url], "error": "", "emails": [{"email": "cto@acme-data.test"}]}
    monkeypatch.setattr("leabee.extractors.enrich_site", fake_enrich)
    res = enrich_sites({"min_score": 35}, store, limit=5, min_score=30)
    assert res == {"crawled": 1, "with_contact": 1} and calls == ["https://acme-data.test"]
    after = store.get(lead["id"])
    assert "cto@acme-data.test" in after["contact"] and after["score"] > before["score"]
    d = json.loads(after["details"])
    assert d["site"]["best_email"] == "cto@acme-data.test" and d["website"] == "https://acme-data.test" and d["company"] == "Acme"
    assert enrich_sites({"min_score": 35}, store, limit=5, min_score=30) == {"crawled": 0, "with_contact": 0}   # each site is crawled once
