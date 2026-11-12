"""LLM client (OpenRouter / Ollama), Browser Use adapter, and the free OpenStreetMap business finder. All data is invented."""
import json
import sys
import types
from types import SimpleNamespace

import pytest

from leabee import discovery, extractors as X, llm
from leabee.copilot import spec, tools
from leabee.discovery import osm, overture
from leabee.extractors import agents, base
from leabee.storage import Store
from leabee.web import create_app


def _clean(monkeypatch):
    for v in ("OPENROUTER_API_KEY", "LEABEE_LLM_BASE_URL", "LEABEE_LLM_API_KEY", "LEABEE_LLM_MODEL"):
        monkeypatch.delenv(v, raising=False)


# ----------------------------------------------------------------------------- LLM client
def test_llm_settings_openrouter_and_ollama(monkeypatch):
    _clean(monkeypatch)
    assert llm.available() is False and "off" in llm.describe()
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    s = llm.settings()
    assert s["openrouter"] and s["base_url"] == "https://openrouter.ai/api/v1" and all(m.endswith(":free") for m in s["models"])
    monkeypatch.setenv("LEABEE_LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LEABEE_LLM_MODEL", "llama3.2")
    monkeypatch.delenv("OPENROUTER_API_KEY")
    s = llm.settings()
    assert not s["openrouter"] and s["models"] == ["llama3.2"] and s["api_key"] == "local" and llm.available()      # Ollama needs no key


def test_llm_complete_tries_next_free_model_and_parses_json(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("LEABEE_LLM_MODEL", "a:free,b:free,c:free")
    calls = []

    def post(url, headers=None, json=None, timeout=0):
        calls.append((url, json["model"], headers["Authorization"]))
        code = {"a:free": 429, "b:free": 200}.get(json["model"], 500)
        body = {"choices": [{"message": {"content": "```json\n{\"x\": 1}\n```"}}]}
        return SimpleNamespace(status_code=code, json=lambda: body, text="")
    monkeypatch.setattr(llm.requests, "post", post)
    assert llm.complete_json("p") == {"x": 1}
    assert [c[1] for c in calls] == ["a:free", "b:free"] and calls[0][0].endswith("/chat/completions") and calls[0][2] == "Bearer k"
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: SimpleNamespace(status_code=500, json=lambda: {}, text=""))
    with pytest.raises(llm.LLMError):
        llm.complete("p")
    assert llm.complete_json("p") is None


# ----------------------------------------------------------------------------- Browser Use (open source) adapter
def test_browser_use_adapter_with_fake_sdk_and_status(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setattr(agents, "is_public_url", lambda u: True)
    bu = types.ModuleType("browser_use")
    reply = {"emails": ["boss@z.co"], "phones": []}

    class Result:
        def final_result(self): return "Here: " + json.dumps(reply)

    class Agent:
        def __init__(self, task, llm=None, browser=None): bu.task, bu.llm = task, llm
        async def run(self): return Result()
    bu.Agent, bu.Browser = Agent, lambda *a, **k: "browser"
    bu.ChatOpenAI = lambda model, base_url=None, api_key=None: SimpleNamespace(model=model, base_url=base_url, api_key=api_key)
    monkeypatch.setitem(sys.modules, "browser_use", bu)
    assert agents.BrowserUseBackend().available()[0] is False                         # installed but no LLM configured
    monkeypatch.setenv("LEABEE_LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LEABEE_LLM_MODEL", "llama3.2")
    assert agents.BrowserUseBackend().available()[0] is True
    assert agents.BrowserUseBackend().extract("https://z.co", "find contacts", agents.CONTACT_FIELDS) == reply
    assert "Do not log in" in bu.task and bu.llm.base_url == "http://localhost:11434/v1" and bu.llm.model == "llama3.2"
    monkeypatch.setattr(agents, "is_public_url", lambda u: False)
    assert agents.BrowserUseBackend().extract("http://169.254.169.254/", "x", {"a": "b"}) == {}


def test_tools_status_and_json_helper():
    assert {t["name"] for t in X.status()} == {"plain", "crawl4ai", "tinyfish", "browser_use"}
    assert agents._json_from('Sure!\n```json\n{"emails": ["a@b.co"]}\n```') == {"emails": ["a@b.co"]} and agents._json_from("none") is None


def test_enrich_site_uses_agent_only_when_asked(monkeypatch):
    empty = base.parse_html("https://quiet.test/", "<p>" + "A quiet page without any contact details at all. " * 6 + "</p>")
    empty.backend = "plain"

    class Plain:
        name = "plain"
        def available(self): return True, ""
        def fetch(self, url, timeout=0): return empty

    class FakeAgent:
        def available(self): return True, ""
        def extract(self, url, goal, fields): return {"emails": ["owner@quiet.test"], "phones": ["+1 555 010 9999"], "owner_or_contact_name": "Sam Owner"}
    monkeypatch.setitem(X.PAGE_BACKENDS, "plain", Plain)
    monkeypatch.setitem(X.AGENT_BACKENDS, "browser_use", FakeAgent)
    monkeypatch.setattr(X, "email_domain_resolves", lambda e: True)
    cfg = {"extractors": {"page_order": ["plain"]}}
    assert X.enrich_site("https://quiet.test/", cfg)["best_email"] == ""
    info = X.enrich_site("https://quiet.test/", cfg, use_agents=True)
    assert info["best_email"] == "owner@quiet.test" and info["owner_or_contact_name"] == "Sam Owner" and info["backend"].endswith("+browser_use")


# ----------------------------------------------------------------------------- request parsing
@pytest.mark.parametrize("text,trade,where,n", [
    ("provide me the details of plumbers in north carolina", "plumbers", "north carolina", 12),
    ("Give me 15 electricians near Raleigh, NC please", "electricians", "Raleigh, NC", 15),
    ("list the top 10 best dentists in Austin Texas", "dentists", "Austin Texas", 10),
])
def test_parse_request_without_llm(monkeypatch, text, trade, where, n):
    _clean(monkeypatch)
    assert discovery.parse_request(text) == {"trade": trade, "where": where, "n": n}
    assert "error" in discovery.parse_request("hello there")


def test_parse_request_falls_back_to_llm(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: {"trade": "plumbers", "where": "Ohio", "n": 8})
    assert discovery.parse_request("plumbers ohio, eight of them")["where"] == "Ohio"


def test_trade_mapping_and_queries():
    assert osm.resolve_trade("plumbers") == ("plumber", ['["craft"="plumber"]'])
    assert osm.resolve_trade("hvac contractors")[0] == "HVAC contractor" and osm.resolve_trade("dentists")[1] == ['["amenity"="dentist"]']
    label, f = osm.resolve_trade("falconers")
    assert f == ['["name"~"falcon",i]']                                                  # unknown trades fall back to a name match
    q = osm.build_query(['["craft"="plumber"]'], {"type": "relation", "id": 224045, "bbox": []})
    assert "area(id:3600224045)" in q and 'nwr["craft"="plumber"](area.a);' in q and q.endswith("out center tags 400;")
    q2 = osm.build_query(['["craft"="plumber"]'], {"type": "node", "id": 1, "bbox": [35.0, 36.0, -80.0, -79.0]})
    assert "(35.0,-80.0,36.0,-79.0)" in q2


def test_element_to_record_and_dedupe():
    el = {"type": "node", "id": 7, "lat": 35.1, "lon": -80.2, "tags": {"name": "Ace Plumbing", "contact:phone": "+1 704 555 0101", "website": "aceplumbing.test",
          "contact:email": "Info@AcePlumbing.test", "addr:housenumber": "12", "addr:street": "Main St", "addr:city": "Charlotte", "addr:state": "NC",
          "addr:postcode": "28202", "opening_hours": "Mo-Fr 08:00-17:00", "contact:facebook": "https://facebook.com/ace"}}
    r = osm.element_to_record(el, "plumber")
    assert r["website"] == "https://aceplumbing.test" and r["email"] == "info@aceplumbing.test" and r["address"] == "12 Main St, Charlotte, NC 28202"
    assert r["source"] == "https://www.openstreetmap.org/node/7" and r["socials"] == {"facebook": "https://facebook.com/ace"}
    assert osm.element_to_record({"type": "node", "id": 1, "tags": {"craft": "plumber"}}, "plumber") is None                # no name -> dropped
    dup = {**r, "osm_id": "node/8", "email": ""}
    other = {**r, "name": "Beta Pipes", "website": "", "phone": "+1 704 555 0101", "email": ""}                          # same phone
    third = {**r, "name": "Gamma", "website": "", "phone": "", "email": "", "city": "Raleigh"}
    out = discovery._dedupe([dup, other, r, third])
    assert [b["name"] for b in out] == ["Ace Plumbing", "Gamma"]


# ----------------------------------------------------------------------------- full flow with fake free services
def _els(n):
    return [{"type": "node", "id": i, "lat": 35, "lon": -80, "tags": {"name": f"Plumber {i}", "phone": f"+1 919 555 {1000 + i}", "addr:city": "Raleigh",
            **({"website": f"https://p{i}.test"} if i % 2 == 0 else {})}} for i in range(1, n + 1)]


@pytest.fixture
def fake_osm(monkeypatch):
    _clean(monkeypatch)
    calls = {"overpass": 0}
    monkeypatch.setattr(overture, "search", lambda *a, **k: [])               # never touch the network in tests
    monkeypatch.setattr(osm, "geocode", lambda place: {"name": "North Carolina, United States", "type": "relation", "id": 224045, "bbox": [], "address": {}})
    def op(q, timeout=0):
        calls["overpass"] += 1
        return _els(10)
    monkeypatch.setattr(osm, "overpass", op)

    def enrich(b, cfg):
        b = dict(b)
        has = bool(b.get("website"))
        b["verified"] = {"website": has or None, "email_domain": None}
        if has:
            b["email"], b["email_source"] = f"info@{b['website'].split('//')[1]}", "website"
            b["verified"]["email_domain"] = True
            b["verified"]["email_matches_website"] = True
        return b
    monkeypatch.setattr(discovery, "_enrich_one", enrich)
    return calls


def test_find_businesses_ranks_complete_ones_first_and_is_honest(fake_osm):
    res = discovery.find_businesses("plumbers in North Carolina", 4, {})
    assert res["query"]["trade"] == "plumber" and len(res["businesses"]) == 4
    assert all(b["website"] and b["email"] and b["phone"] for b in res["businesses"])                      # complete ones first
    assert res["stats"]["fully_complete"] == 4 and res["stats"]["found_on_map"] == 10
    res = discovery.find_businesses("plumbers in North Carolina", 12, {})
    assert len(res["businesses"]) == 10 and any("Only 10" in n for n in res["notes"])                       # fewer than requested: say so
    assert any("no email" in n for n in res["notes"])                                                       # half have no email: say so
    assert all(b["phone"] for b in res["businesses"]) and sum(1 for b in res["businesses"] if not b["email"]) == 5


def test_results_are_cached_and_errors_are_clear(fake_osm, monkeypatch):
    s = Store(":memory:")
    a = discovery.find_businesses("plumbers in North Carolina", 3, {}, store=s)
    b = discovery.find_businesses("plumbers in North Carolina", 3, {}, store=s)
    assert fake_osm["overpass"] == 1 and "cache" in b["notes"][0] and [x["name"] for x in a["businesses"]] == [x["name"] for x in b["businesses"]]
    assert "what and where" in discovery.find_businesses("hello", None, {})["error"]
    monkeypatch.setattr(osm, "geocode", lambda p: None)
    assert "could not find the place" in discovery.find_businesses("plumbers in Nowhereville", None, {})["error"]
    monkeypatch.setattr(osm, "geocode", lambda p: {"name": "X", "type": "relation", "id": 1, "bbox": [], "address": {}})
    monkeypatch.setattr(osm, "overpass", lambda q, timeout=0: (_ for _ in ()).throw(RuntimeError("HTTP 504")))
    assert "busy" in discovery.find_businesses("plumbers in X", None, {})["error"]


def test_save_to_pipeline_api_cli_tool_and_policy(fake_osm):
    s = Store(":memory:")
    res = discovery.find_businesses("plumbers in North Carolina", 4, {})
    out = discovery.save_to_pipeline(s, {"min_score": 35, "gmaps": {}}, res["businesses"])
    assert out["saved"] == 4 and s.query(kind="prospect")[1] == 4
    lead = s.query(kind="prospect")[0][0]
    assert lead["platform"] == "OpenStreetMap" and lead["contact"] and "reviews" not in lead["reasons"].lower().split("need:")[1].split("|")[0]
    assert json.loads(lead["details"])["verified"]["website"] is True
    app = create_app({"min_score": 35, "max_age_days": 99999, "hot_score": 65}, s).test_client()
    assert app.post("/api/discover", json={}).status_code == 400
    r = app.post("/api/discover", json={"query": "plumbers in North Carolina", "n": 3, "save": True})
    assert r.status_code == 200 and len(r.get_json()["businesses"]) == 3 and r.get_json()["saved"]["saved"] == 3
    t = tools.find_businesses(s, {"min_score": 35}, "plumbers in North Carolina", 3)
    assert t["businesses"][0]["phone"] and all(v for b in t["businesses"] for v in (b["email"], b["website"], b["address"]))   # "not found" never blank
    assert "find_businesses" in {n for _, _, names in spec.POLICIES for n in names}
    text = " ".join(c for _, c, _ in spec.POLICIES).lower()
    assert "never add or guess any detail" in text and "only save when the user says yes" in text


def test_phone_normalisation_and_cross_source_merge(monkeypatch):
    assert discovery.normalize_phone("19197724523") == "+1 919 772 4523" and discovery.normalize_phone("(704) 755-0228") == "+1 704 755 0228"
    assert discovery.normalize_phone("+1-910-616-3238;+1-910-769-5090") == "+1 910 616 3238" and discovery.normalize_phone("+44 20 7946 0958") == "+44 20 7946 0958"
    assert discovery._norm_site("acme.test") == "https://acme.test" and discovery._norm_site("") == ""
    osm_rec = {"name": "Ace Electric", "phone": "+1 919 555 0101", "website": "", "email": "", "address": "", "city": "Raleigh", "socials": {}, "source": "osm"}
    ov_rec = {"name": "ACE ELECTRIC LLC", "phone": "+1 919 555 0101", "website": "https://ace.test", "email": "hi@ace.test", "address": "1 Main St", "city": "Raleigh",
              "socials": {"facebook": "https://facebook.com/ace"}, "source": "overture"}
    out = discovery._dedupe([osm_rec, ov_rec])
    assert len(out) == 1 and out[0]["website"] == "https://ace.test" and out[0]["email"] == "hi@ace.test" and out[0]["socials"]["facebook"]
    assert out[0]["sources"] == ["osm", "overture"]                                       # both sources credited


def test_overture_helpers(monkeypatch):
    monkeypatch.setenv("LEABEE_OVERTURE_RELEASE", "2099-01-01.0")
    overture.latest_release.cache_clear()
    assert overture.latest_release() == "2099-01-01.0"
    assert overture.terms_for("plumber", "plumbers") == ["plumb"] and overture.terms_for("falconer", "falconers") == ["falcon"]
    assert overture.search([], [1, 2, 3, 4]) == []


def test_one_source_down_still_answers_and_both_down_errors(monkeypatch, fake_osm):
    monkeypatch.setattr(osm, "overpass", lambda q, timeout=0: (_ for _ in ()).throw(RuntimeError("HTTP 504")))
    monkeypatch.setattr(overture, "search", lambda *a, **k: [{"name": "Zed Plumbing", "trade": "plumbing", "phone": "9195550000", "website": "zed.test", "email": "", "address": "",
                                                              "city": "Raleigh", "state": "NC", "postcode": "", "hours": "", "socials": {}, "source": "ov", "osm_id": "overture/1"}])
    res = discovery.find_businesses("plumbers in North Carolina", 3, {})
    assert [b["name"] for b in res["businesses"]] == ["Zed Plumbing"] and res["businesses"][0]["phone"] == "+1 919 555 0000"
    assert any("unavailable" in n for n in res["notes"]) and "Overture Maps (1)" in res["notes"][0]
    monkeypatch.setattr(overture, "search", lambda *a, **k: (_ for _ in ()).throw(overture.OvertureUnavailable("down")))
    assert "busy or unreachable" in discovery.find_businesses("plumbers in North Carolina", 3, {})["error"]


def test_enrich_until_returns_on_time_and_keeps_unfinished(monkeypatch):
    import time as _t
    from leabee import discovery as d

    def slow(b, cfg):
        if b["name"] == "slow":
            _t.sleep(3)
        return {**b, "done": True}
    monkeypatch.setattr(d, "_enrich_one", slow)
    start = _t.time()
    out = d._enrich_until([{"name": "fast"}, {"name": "slow"}], {}, 4, _t.time() + 1.2)
    assert _t.time() - start < 2.5
    assert out[0].get("done") and not out[1].get("done")


def test_copilot_hidden_and_refused_on_vercel(monkeypatch):
    from leabee.config import load_config
    from leabee.storage import Store
    from leabee.web import create_app
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("PARLANT_URL", raising=False)
    monkeypatch.delenv("LEABEE_PASSWORD", raising=False)
    monkeypatch.delenv("LEABEE_PASSWORD_HASH", raising=False)
    c = create_app(load_config(), Store(":memory:")).test_client()
    assert b'id="copilotbtn"' not in c.get("/").data
    assert c.post("/api/copilot/ask", json={"message": "hi"}, headers={"Origin": "http://localhost"}).status_code == 503
