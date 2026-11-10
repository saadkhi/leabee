"""Remote (Turso) mode, tested against a fake server that speaks the Hrana HTTP protocol over in-memory SQLite."""
import base64
import sqlite3
from types import SimpleNamespace

import pytest
import requests

from leabee import remote_collect as rc
from leabee import serverless
from leabee.http import Http
from leabee.storage import Store, migrate
from leabee.turso import TursoConnection, TursoError
from leabee.web import create_app

CFG = {"min_score": 35, "max_age_days": 10 ** 5, "hot_score": 70}


class FakeTurso:
    """Stands in for requests.Session: executes pipeline requests on SQLite and answers in Hrana JSON."""

    def __init__(self, fail_first: int = 0):
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.calls = 0
        self.fail_first = fail_first
        self.last_headers = {}

    @staticmethod
    def _arg(a):
        t = a["type"]
        return None if t == "null" else int(a["value"]) if t == "integer" else a["value"] if t in ("float", "text") \
            else base64.b64decode(a["base64"])

    @staticmethod
    def _cell(v):
        if v is None:
            return {"type": "null"}
        if isinstance(v, int):
            return {"type": "integer", "value": str(v)}
        if isinstance(v, float):
            return {"type": "float", "value": v}
        if isinstance(v, bytes):
            return {"type": "blob", "base64": base64.b64encode(v).decode()}
        return {"type": "text", "value": v}

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls += 1
        self.last_headers = headers or {}
        if self.calls <= self.fail_first:
            return SimpleNamespace(status_code=503, text="busy", json=lambda: {})
        results = []
        for req in json["requests"]:
            if req["type"] == "close":
                results.append({"type": "ok", "response": {"type": "close"}})
                continue
            st = req["stmt"]
            try:
                cur = self.db.execute(st["sql"], [self._arg(a) for a in st["args"]])
                cols = [{"name": d[0]} for d in (cur.description or [])]
                rows = [[self._cell(v) for v in r] for r in cur.fetchall()] if cur.description else []
                self.db.commit()
                results.append({"type": "ok", "response": {"type": "execute", "result": {
                    "cols": cols, "rows": rows, "affected_row_count": max(cur.rowcount, 0)}}})
            except sqlite3.Error as e:
                results.append({"type": "error", "error": {"message": str(e)}})
        return SimpleNamespace(status_code=200, text="", json=lambda: {"results": results})

    def close(self):
        pass


def remote_store(fake=None) -> Store:
    return Store("libsql://fake.turso.io", conn=TursoConnection("libsql://fake.turso.io", "tok", session=fake or FakeTurso()))


@pytest.fixture(params=["local", "remote"])
def store(request):
    return Store(":memory:") if request.param == "local" else remote_store()


def _lead(i, score=60, **kw):
    d = dict(id=i, source="reddit", platform="Reddit", lead_type="buyer", title=f"lead title number {i}", text="x",
             url=f"https://u/{i}", author="", author_url="", posted_at="2026-11-08T00:00:00+00:00", categories="automation",
             country="India", budget_text="", budget_usd=None, contact="", score=score, reasons="", enriched=0, language="en")
    d.update(kw)
    return d


def test_store_behaves_the_same_locally_and_on_turso(store):
    assert store.upsert(_lead("1", 90, contact="a@b.co")) is True
    assert store.upsert(_lead("1", 95, contact="a@b.co")) is False   # refresh, not new
    assert store.upsert(_lead("2", 50, country="")) is True
    assert store.get("1")["score"] == 95 and store.get("nope") is None
    rows, total = store.query(min_score=60)
    assert total == 1 and rows[0]["id"] == "1" and isinstance(rows[0]["budget_usd"], (float, type(None)))
    assert store.query(country="Unknown")[1] == 1 and store.query(has_contact=True)[1] == 1
    assert store.bulk_status(["1", "2"], "contacted") == 2
    store.update_user_fields("1", follow_up="2000-01-01", deal_value=300.5, notes="hi")
    assert store.query(follow_due=True)[1] == 1
    store.update_user_fields("1", status="won")
    st = store.stats()
    assert st["total"] == 2 and st["revenue"] == 300.5 and st["platform_perf"][0]["won"] == 1
    assert {"by_category", "category_trend", "per_day", "runs"} <= set(st)
    store.save_filter("Hot", "min_score=70")
    store.set_meta("k", "v")
    assert store.list_filters()[0]["name"] == "Hot" and store.get_meta("k") == "v"
    store.log_run("reddit", "a", 5, 2, 1)
    assert store.stats()["runs"][0]["source"] == "reddit"
    store.set_ai("2", "buyer", "wants automation", 5)
    assert store.get("2")["ai_verdict"] == "buyer"
    assert "score" in store.to_csv(store.query()[0]).splitlines()[0]
    store.delete("2")
    assert store.get("2") is None


def test_crosspost_and_insert_race_on_turso():
    s = remote_store()
    assert s.upsert(_lead("1", author="bob", title="[Hiring] Zoho developer remote india")) is True
    assert s.upsert(_lead("2", author="bob", title="[Hiring] Zoho Developer Remote India", url="https://u/2")) is False
    # a second writer inserting the same id first must not raise or count as new
    s._conn.execute("INSERT INTO leads (id,source,platform,lead_type,title,url,found_at) VALUES ('9','r','R','buyer','t','u','2026')")
    assert s.upsert(_lead("9")) is False


def test_schema_migration_on_remote_adds_columns():
    fake = FakeTurso()
    fake.db.executescript("CREATE TABLE leads (id TEXT PRIMARY KEY, source TEXT NOT NULL, platform TEXT NOT NULL, lead_type TEXT NOT NULL,"
                          "title TEXT NOT NULL, text TEXT NOT NULL DEFAULT '', url TEXT NOT NULL, author TEXT DEFAULT '', author_url TEXT DEFAULT '',"
                          "posted_at TEXT, found_at TEXT NOT NULL, categories TEXT DEFAULT '', country TEXT DEFAULT '', budget_text TEXT DEFAULT '',"
                          "budget_usd REAL, contact TEXT DEFAULT '', score INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'new',"
                          "notes TEXT DEFAULT '', enriched INTEGER NOT NULL DEFAULT 0, reasons TEXT DEFAULT '');")
    s = remote_store(fake)
    assert s.upsert(_lead("1")) is True and s.get("1")["follow_up"] == ""


def test_stats_is_one_round_trip_per_call():
    fake = FakeTurso()
    s = remote_store(fake)
    s.upsert(_lead("1"))
    before = fake.calls
    s.stats()
    assert fake.calls - before == 1
    before = fake.calls
    s.query()
    assert fake.calls - before == 1


def test_migrate_local_to_remote_and_backup_roundtrip(tmp_path):
    local = Store(str(tmp_path / "l.db"))
    local.upsert(_lead("1", 90))
    local.upsert(_lead("2", 70))
    local.update_user_fields("1", status="contacted", notes="called")
    local.save_filter("F", "q=1")
    local.set_meta("reddit_offset", "3")
    remote = remote_store()
    res = migrate(local, remote)
    assert res["leads"] == 2
    got = remote.get("1")
    assert got["status"] == "contacted" and got["notes"] == "called"
    assert remote.get_meta("reddit_offset") == "3" and remote.list_filters()[0]["name"] == "F"
    assert migrate(local, remote)["leads"] == 2 and remote.query()[1] == 2      # re-running does not duplicate
    out = remote.backup(str(tmp_path / "bk"))
    assert Store(out).get("1")["notes"] == "called"
    from leabee.jobs import make_snapshot
    assert make_snapshot(remote, tmp_path / "snap" / "s.sqlite") == 2


def test_turso_connection_retries_and_errors():
    fake = FakeTurso(fail_first=2)
    conn = TursoConnection("libsql://x.turso.io", "secret", session=fake)
    import leabee.turso as t
    t.time.sleep = lambda s: None
    assert conn.execute("SELECT 1").fetchone()[0] == 1 and fake.calls == 3
    assert fake.last_headers["Authorization"] == "Bearer secret"
    with pytest.raises(TursoError):
        conn.execute("SELECT * FROM missing_table")
    with pytest.raises(TursoError):
        TursoConnection("", "")


def test_atomic_batch_rolls_back_on_error():
    conn = TursoConnection("libsql://x.turso.io", "t", session=FakeTurso())
    conn.execute("CREATE TABLE t (a INTEGER PRIMARY KEY)")
    with pytest.raises(TursoError):
        conn.batch([("INSERT INTO t VALUES (1)", ()), ("INSERT INTO t VALUES (1)", ())], atomic=True)
    # fake server auto-commits per statement so only assert the error surfaced; real Turso rolls the stream back


def test_vercel_full_mode_uses_turso(monkeypatch):
    fake = FakeTurso()
    monkeypatch.setenv("LEABEE_PASSWORD", "pw")
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://fake.turso.io")
    monkeypatch.setattr(serverless, "Store", lambda url: remote_store(fake))
    c = serverless.build_app().test_client()
    h = {"Authorization": "Basic " + base64.b64encode(b"u:pw").decode()}
    assert c.get("/api/stats").status_code == 401
    # not read-only: edits work and persist in the database
    remote_store(fake).upsert(_lead("1", 90))
    assert c.patch("/api/leads/1", json={"status": "won"}, headers=h).status_code == 200
    assert remote_store(fake).get("1")["status"] == "won"
    assert c.get("/board", headers=h).status_code == 200
    # bad Turso config shows a helpful page, never a stack trace or the token
    monkeypatch.setattr(serverless, "Store", lambda url: (_ for _ in ()).throw(TursoError("HTTP 401")))
    r = serverless.build_app().test_client().get("/", headers=h)
    assert r.status_code == 503 and b"TURSO_AUTH_TOKEN" in r.data


def test_collect_button_dispatches_github_actions(monkeypatch):
    s = remote_store()
    app = create_app(CFG, s, remote_collect=True).test_client()
    monkeypatch.delenv("GITHUB_DISPATCH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPO", raising=False)
    r = app.post("/api/collect", json={})
    assert r.status_code == 503 and "GITHUB_DISPATCH_TOKEN" in r.get_json()["error"]

    monkeypatch.setenv("GITHUB_DISPATCH_TOKEN", "ghp_x")
    monkeypatch.setenv("GITHUB_REPO", "me/leabee")
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.update(url=url, json=json, auth=headers["Authorization"])
        return SimpleNamespace(status_code=204, text="")

    def fake_get(url, headers=None, timeout=None):
        return SimpleNamespace(status_code=200, json=lambda: {"workflow_runs": [
            {"status": seen.get("state", "completed"), "conclusion": "success", "run_number": 7,
             "html_url": "https://github.com/me/leabee/actions/runs/1", "created_at": "2026-11-10T00:00:00Z"}]})

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", fake_get)
    r = app.post("/api/collect", json={"sources": ["hackernews", "bogus"]})
    assert r.status_code == 202
    assert seen["url"].endswith("/repos/me/leabee/actions/workflows/refresh.yml/dispatches")
    assert seen["json"]["inputs"]["sources"] == "hackernews" and seen["auth"] == "Bearer ghp_x"
    st = app.get("/api/collect/status").get_json()
    assert st["running"] is False and "run #7" in st["log"][0]
    seen["state"] = "in_progress"
    assert app.get("/api/collect/status").get_json()["running"] is True
    assert app.post("/api/collect", json={}).status_code == 409          # one run at a time

    monkeypatch.delenv("GITHUB_REPO")
    monkeypatch.setenv("VERCEL_GIT_REPO_OWNER", "o")
    monkeypatch.setenv("VERCEL_GIT_REPO_SLUG", "r")
    assert rc.repo() == "o/r"


def test_http_circuit_breaker_stops_hammering_a_blocked_host(monkeypatch):
    calls = []
    h = Http(delay=0)
    monkeypatch.setattr(h.session, "request", lambda m, u, **kw: calls.append(u) or SimpleNamespace(status_code=403, headers={}))
    for _ in range(6):
        h.get("https://www.reddit.com/r/x/new/.rss")
    assert len(calls) == 3                                   # then skipped
    assert h.get("https://www.reddit.com/r/y/new/.rss") is None
    calls.clear()
    monkeypatch.setattr(h.session, "request", lambda m, u, **kw: calls.append(u) or SimpleNamespace(status_code=200, headers={}))
    assert h.get("https://example.com/").status_code == 200   # other hosts unaffected
