import sqlite3

from leabee import ai, notify
from leabee.classify import detect_country, detect_language, extract_budget
from leabee.collectors import github, reddit
from leabee.outreach import draft
from leabee.storage import Store
from leabee.web import create_app

CFG = {"min_score": 35, "max_age_days": 10 ** 5, "hot_score": 70}


def _lead(i="1", score=60, **kw):
    d = dict(id=i, source="reddit", platform="Reddit", lead_type="buyer", title=f"title {i} long enough here", text="x",
             url=f"https://u/{i}", author="", author_url="", posted_at="2026-11-08T00:00:00+00:00",
             categories="automation", country="India", budget_text="", budget_usd=None, contact="", score=score,
             reasons="", enriched=0, language="en")
    d.update(kw)
    return d


def test_language_and_country_hints():
    assert detect_language("Busco desarrollador web para mi tienda, necesito ayuda") == "es"
    assert detect_language("Need a python developer for my project, looking to hire") == "en"
    assert detect_language("ウェブサイト制作 依頼します") == "ja"
    assert detect_country("budget ₹50,000 need a website") == "India"
    assert detect_country("reach me at x@foo.co.uk") == "United Kingdom"
    assert detect_country("whatsapp +92 300 1234567") == "Pakistan"
    assert detect_country("nothing to see here") == ""


def test_absurd_budget_dropped():
    text, usd = extract_budget("budget $5,000,000,000")
    assert usd is None
    s = Store(":memory:")
    s.upsert(_lead(budget_usd=9e9))
    assert s.get("1")["budget_usd"] is None


def test_migration_adds_columns(tmp_path):
    p = str(tmp_path / "old.db")
    c = sqlite3.connect(p)
    c.executescript("CREATE TABLE leads (id TEXT PRIMARY KEY, source TEXT NOT NULL, platform TEXT NOT NULL, lead_type TEXT NOT NULL,"
                    "title TEXT NOT NULL, text TEXT NOT NULL DEFAULT '', url TEXT NOT NULL, author TEXT DEFAULT '', author_url TEXT DEFAULT '',"
                    "posted_at TEXT, found_at TEXT NOT NULL, categories TEXT DEFAULT '', country TEXT DEFAULT '', budget_text TEXT DEFAULT '',"
                    "budget_usd REAL, contact TEXT DEFAULT '', score INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'new',"
                    "notes TEXT DEFAULT '', enriched INTEGER NOT NULL DEFAULT 0, reasons TEXT DEFAULT '');"
                    "INSERT INTO leads(id,source,platform,lead_type,title,url,found_at) VALUES('a','r','R','buyer','t','u','2026');")
    c.commit(); c.close()
    s = Store(p)
    row = s.get("a")
    assert row["follow_up"] == "" and row["notified"] == 0 and row["language"] == ""


def test_bulk_followup_dealvalue_filters():
    s = Store(":memory:")
    for i in "abc":
        s.upsert(_lead(i, language="es" if i == "a" else "en"))
    assert s.bulk_status(["a", "b"], "contacted") == 2
    s.update_user_fields("a", follow_up="2000-01-01", deal_value=500)
    assert s.query(follow_due=True)[1] == 1
    assert s.query(language="es")[1] == 1
    assert s.query(ids=["c"])[1] == 1 and s.query(ids=[])[1] == 0
    try:
        s.update_user_fields("a", follow_up="not-a-date")
        assert False
    except ValueError:
        pass
    try:
        s.bulk_status(["a"], "bogus")
        assert False
    except ValueError:
        pass
    s.update_user_fields("a", status="won")
    st = s.stats()
    assert st["revenue"] == 500 and st["platform_perf"][0]["won"] == 1


def test_saved_filters_and_meta():
    s = Store(":memory:")
    s.save_filter("Hot", "min_score=70")
    s.save_filter("Hot", "min_score=80")           # same name updates
    fs = s.list_filters()
    assert len(fs) == 1 and fs[0]["query"] == "min_score=80"
    s.delete_filter(fs[0]["id"])
    assert s.list_filters() == []
    s.set_meta("k", "v")
    assert s.get_meta("k") == "v" and s.get_meta("zz", "d") == "d"


def test_ai_parse_and_review_flow(monkeypatch):
    assert ai.parse_verdict('ok {"verdict": "Buyer", "summary": "needs a shopify dev"} done') == ("buyer", "needs a shopify dev")
    assert ai.parse_verdict("garbage") == ("", "")
    assert ai.parse_verdict('{"verdict": "zzz"}') == ("", "")
    s = Store(":memory:")
    s.upsert(_lead("1", 50))
    s.upsert(_lead("2", 50, title="second lead title long enough", author=""))
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    answers = iter([("buyer", "wants automation"), ("reject", "seller")])
    monkeypatch.setattr(ai, "review_one", lambda lead: next(answers))
    res = ai.review({"ai": {}}, s)
    assert res["reviewed"] == 2 and res["rejected"] == 1
    assert s.get("1")["score"] == 55 and s.get("1")["ai_verdict"] == "buyer"
    assert s.get("2")["status"] == "ignored"
    monkeypatch.delenv("OPENROUTER_API_KEY")
    assert "skipped" in ai.review({}, s)


def test_alerts_baseline_then_notify(monkeypatch):
    s = Store(":memory:")
    s.upsert(_lead("1", 90))
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "http://x")
    sent = []
    monkeypatch.setattr(notify, "_discord", lambda text: sent.append(text))
    cfg = {"alerts": {"min_score": 80}}
    assert notify.alert_new_hot(cfg, s)["sent"] == 0       # first run only baselines old leads
    s.upsert(_lead("2", 95, title="brand new hot lead title"))
    assert notify.alert_new_hot(cfg, s)["sent"] == 1 and "brand new hot lead" in sent[0]
    assert notify.alert_new_hot(cfg, s)["sent"] == 0       # not sent twice
    monkeypatch.delenv("DISCORD_WEBHOOK_URL")
    assert notify.channels() == []


def test_outreach_draft():
    d = draft(_lead(categories="content_writing", title="Need a blog writer"))
    assert "content" in d["short"] and set(d) == {"short", "long", "follow_up", "source"}


def test_github_and_reddit_rotation():
    items = github.parse_issues({"items": [
        {"title": "Need dev", "html_url": "https://github.com/a/b/issues/1", "body": "paid project, budget $500", "created_at": "2026-11-07T00:00:00Z",
         "user": {"login": "bob", "html_url": "https://github.com/bob"}},
        {"title": "PR", "html_url": "https://github.com/a/b/pull/2", "pull_request": {}},
        {"title": "Leads — 2026-10-28 (3)", "html_url": "https://github.com/a/b/issues/3", "body": "need hiring", "user": {"login": "x"}},
        {"title": "docs(blog): need post", "html_url": "https://github.com/a/b/issues/4", "body": "", "user": {"login": "x"}},
        {"title": "Report", "html_url": "https://github.com/a/b/issues/5", "body": "paid", "user": {"login": "bot", "type": "Bot"}},
        {"title": "Refactor module", "html_url": "https://github.com/a/b/issues/6", "body": "cleanup the module internals", "user": {"login": "x"}}]})
    assert len(items) == 1 and items[0].platform == "GitHub"
    subs = list(range(10))
    assert reddit._slice(subs, 3, {"_partial": True, "_reddit_offset": 0}) == [0, 1, 2]
    assert reddit._slice(subs, 3, {"_partial": True, "_reddit_offset": 3}) == [9, 0, 1]
    assert reddit._slice(subs, 3, {}) == subs           # manual/full runs sweep everything


def test_web_new_endpoints(monkeypatch):
    s = Store(":memory:")
    s.upsert(_lead("1", 90))
    s.upsert(_lead("2", 80, title="another lead title that is long"))
    app = create_app(CFG, s).test_client()
    for path in ("/", "/insights", "/board"):
        assert app.get(path).status_code == 200
    assert app.get("/api/leads/1/draft").get_json()["short"]
    assert app.get("/api/leads/nope/draft").status_code == 404
    assert app.post("/api/leads/bulk", json={"ids": ["1", "2"], "status": "contacted"}).get_json()["updated"] == 2
    assert app.post("/api/leads/bulk", json={"ids": ["1"], "status": "bad"}).status_code == 400
    assert app.patch("/api/leads/1", json={"follow_up": "2030-01-01", "deal_value": 250}).status_code == 200
    assert app.patch("/api/leads/1", json={"follow_up": "soon"}).status_code == 400
    assert app.post("/api/filters", json={"name": "x", "query": "min_score=50"}).status_code == 201
    fid = app.get("/api/filters").get_json()[0]["id"]
    assert app.delete(f"/api/filters/{fid}").status_code == 200
    csv = app.get("/export.csv?ids=1").data.decode("utf-8-sig")
    assert csv.count("\n") == 2                         # header + only the selected lead
    j = app.get("/api/stats").get_json()
    assert {"platform_perf", "category_trend", "by_language", "follow_ups_due"} <= set(j)


def test_password_protection(monkeypatch):
    monkeypatch.setenv("LEABEE_PASSWORD", "correct-horse-battery")
    monkeypatch.setenv("LEABEE_ALLOW_BASIC", "1")
    c = create_app(CFG, Store(":memory:")).test_client()
    assert c.get("/api/stats").status_code == 401
    import base64
    ok = {"Authorization": "Basic " + base64.b64encode(b"me:correct-horse-battery").decode()}
    bad = {"Authorization": "Basic " + base64.b64encode(b"me:wrong-password-here").decode()}
    assert c.get("/api/stats", headers=ok).status_code == 200
    assert c.get("/api/stats", headers=bad).status_code == 401


def test_backup(tmp_path):
    s = Store(str(tmp_path / "leads.db"))
    s.upsert(_lead("1"))
    out = s.backup(str(tmp_path / "bk"))
    assert Store(out).get("1") is not None
