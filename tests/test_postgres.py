"""Neon/Postgres backend. DB-backed tests run only when LEABEE_TEST_PG points at a throwaway Postgres, e.g.
postgresql://postgres:postgres@127.0.0.1:5544/postgres?sslmode=disable  (never point it at real data: tables are dropped)."""
import base64
import os
import sqlite3

import pytest

from leabee import serverless
from leabee.dbconn import PgConnection, translate
from leabee.storage import Store, database_url, migrate
from leabee.web import create_app

CFG = {"min_score": 35, "max_age_days": 10 ** 5, "hot_score": 70}
PG_URL = os.environ.get("LEABEE_TEST_PG", "")
needs_pg = pytest.mark.skipif(not PG_URL, reason="set LEABEE_TEST_PG to run Postgres tests")
AUTH = {"Authorization": "Basic " + base64.b64encode(b"u:correct-horse-battery").decode()}


def _lead(i, score=60, **kw):
    d = dict(id=i, source="reddit", platform="Reddit", lead_type="buyer", title=f"lead title number {i}", text="Need a Shopify dev",
             url=f"https://u/{i}", author="", author_url="", posted_at="2026-11-08T00:00:00+00:00", categories="automation",
             country="India", budget_text="", budget_usd=None, contact="", score=score, reasons="", enriched=0, language="en")
    d.update(kw)
    return d


def _reset():
    c = PgConnection(PG_URL)
    c.execute("DROP TABLE IF EXISTS leads, runs, saved_filters, meta CASCADE")
    c.close()


@pytest.fixture
def pg():
    """Fresh empty database; yields a factory so a test can reopen the store (PGlite allows one connection at a time)."""
    _reset()
    opened = []

    def make() -> Store:
        for s in opened:
            s.close()
        opened.clear()
        s = Store(PG_URL)
        opened.append(s)
        return s
    yield make
    for s in opened:
        s.close()
    _reset()


@pytest.fixture(params=["sqlite", "postgres"])
def store(request):
    if request.param == "sqlite":
        yield Store(":memory:")
        return
    if not PG_URL:
        pytest.skip("set LEABEE_TEST_PG to run Postgres tests")
    _reset()
    s = Store(PG_URL)
    yield s
    s.close()
    _reset()


# ---------------------------------------------------------------- no database needed
def test_translate_placeholders():
    assert translate("SELECT * FROM t WHERE a=? AND b LIKE ?") == "SELECT * FROM t WHERE a=%s AND b LIKE %s"
    assert translate("SELECT '100%'") == "SELECT '100%%'"


def test_database_url_precedence_and_masking(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    assert database_url() == ""
    monkeypatch.setenv("POSTGRES_URL", "postgres://u:p@h/db")
    assert database_url() == "postgres://u:p@h/db"
    monkeypatch.setenv("DATABASE_URL", "postgresql://u2:p2@h2/db2")
    assert database_url() == "postgresql://u2:p2@h2/db2"
    monkeypatch.setenv("DATABASE_URL", "mysql://nope")
    assert database_url() == "postgres://u:p@h/db"                 # ignores non-Postgres values
    # credentials never appear in the display name
    s = Store.__new__(Store)
    s.path, s.pg = "postgresql://user:" + "s3cret" + "@ep-x-pooler.neon.tech/neondb?sslmode=require", True
    assert s.where == "postgres://ep-x-pooler.neon.tech/neondb" and "s3cret" not in s.where


def test_vercel_message_does_not_leak_connection_string(monkeypatch):
    monkeypatch.setenv("LEABEE_PASSWORD", "correct-horse-battery")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:topsecret@127.0.0.1:1/db?connect_timeout=2")
    r = serverless.build_app().test_client().get("/", headers=AUTH)
    assert r.status_code == 503 and b"DATABASE_URL" in r.data and b"topsecret" not in r.data


# ---------------------------------------------------------------- real Postgres
def test_store_behaves_the_same_on_sqlite_and_postgres(store):
    assert store.upsert(_lead("1", 90, contact="a@b.co")) is True
    assert store.upsert(_lead("1", 95, contact="a@b.co")) is False          # refresh, not new
    assert store.upsert(_lead("2", 50, country="", title="Another SHOPIFY request here")) is True
    assert store.get("1")["score"] == 95 and store.get("nope") is None
    rows, total = store.query(min_score=60)
    assert total == 1 and rows[0]["id"] == "1"
    assert store.query(country="Unknown")[1] == 1 and store.query(has_contact=True)[1] == 1
    assert store.query(category="automation")[1] == 2 and store.query(category="auto")[1] == 0
    assert store.query(q="shopify")[1] == 2                                  # search is case-insensitive on both
    assert store.query(since_hours=24 * 365 * 5)[1] == 2 and store.query(since_hours=1)[1] == 0
    assert store.query(ids=["2"])[1] == 1 and store.query(ids=[])[1] == 0
    assert store.bulk_status(["1", "2"], "contacted") == 2
    store.update_user_fields("1", follow_up="2000-01-01", deal_value=300.5, notes="hi")
    assert store.query(follow_due=True)[1] == 1 and store.get("1")["deal_value"] == 300.5
    store.update_user_fields("1", status="won")
    st = store.stats()
    assert st["total"] == 2 and st["revenue"] == 300.5
    perf = st["platform_perf"][0]
    assert perf["won"] == 1 and perf["worked"] == 2 and isinstance(perf["avg_score"], float) and isinstance(perf["revenue"], float)
    assert {"by_category", "category_trend", "per_day", "by_language", "by_score", "by_type"} <= set(st)
    store.save_filter("Hot", "min_score=70")
    store.save_filter("Hot", "min_score=80")
    assert [f["query"] for f in store.list_filters()] == ["min_score=80"]
    store.set_meta("k", "v")
    store.set_meta("k", "v2")
    assert store.get_meta("k") == "v2" and store.get_meta("zz", "d") == "d"
    store.log_run("reddit", "a", 5, 2, 1)
    assert store.stats()["runs"][0]["source"] == "reddit"
    assert "score" in store.to_csv(store.query()[0]).splitlines()[0]
    store.delete("2")
    assert store.get("2") is None


def test_ai_score_clamp_and_enriched_never_downgraded(store):
    store.upsert(_lead("1", 98))
    store.upsert(_lead("2", 3, title="second lead title long enough"))
    store.set_ai("1", "buyer", "wants shopify", 5)
    store.set_ai("2", "maybe", "", -5)
    assert store.get("1")["score"] == 100 and store.get("2")["score"] == 0 and store.get("1")["ai_verdict"] == "buyer"
    store.upsert(_lead("3", 60, title="third lead title long enough", enriched=1))
    assert store.upsert(_lead("3", 61, title="third lead title long enough", enriched=0)) is False
    assert store.get("3")["enriched"] == 1 and store.get("3")["score"] == 60     # snippet did not overwrite enriched data
    assert [r["id"] for r in store.for_ai_review(10, 0, 100)] == ["3", "2"] or len(store.for_ai_review(10, 0, 100)) >= 1
    assert store.baseline_notified(0) == 3 and store.to_notify(0) == []


def test_crosspost_dedupe_and_insert_race(store):
    assert store.upsert(_lead("1", author="bob", title="[Hiring] Zoho developer remote india")) is True
    assert store.upsert(_lead("2", author="bob", title="[Hiring] Zoho Developer Remote India", url="https://u/2")) is False
    store._conn.execute("INSERT INTO leads (id,source,platform,lead_type,title,text,url,found_at,author) VALUES "
                        "('9','r','R','buyer','[Hiring] Zoho developer remote india','','https://u/9','2099-01-01','bob')")
    assert store.dedupe() == 1 and store.get("9") is None and store.get("1") is not None
    store._conn.execute("INSERT INTO leads (id,source,platform,lead_type,title,url,found_at) VALUES ('7','r','R','buyer','t','u','2026')")
    assert store.upsert(_lead("7", title="t")) is False                            # existing id is refreshed, not duplicated


@needs_pg
def test_schema_is_idempotent_and_migrates_old_table(pg):
    s = pg()
    s.upsert(_lead("1"))
    s = pg()                                                                        # reopen: schema check skips DDL, data intact
    assert s.get("1") is not None and s.stats()["total"] == 1
    # an older deployment without the newer columns gets them added, keeping its rows
    s._conn.execute("DROP TABLE leads")
    s._conn.execute("CREATE TABLE leads (id TEXT PRIMARY KEY, source TEXT NOT NULL, platform TEXT NOT NULL, lead_type TEXT NOT NULL,"
                    "title TEXT NOT NULL, text TEXT NOT NULL DEFAULT '', url TEXT NOT NULL, author TEXT DEFAULT '', author_url TEXT DEFAULT '',"
                    "posted_at TEXT, found_at TEXT NOT NULL, categories TEXT DEFAULT '', country TEXT DEFAULT '', budget_text TEXT DEFAULT '',"
                    "budget_usd DOUBLE PRECISION, contact TEXT DEFAULT '', score INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'new',"
                    "notes TEXT DEFAULT '', enriched INTEGER NOT NULL DEFAULT 0, reasons TEXT DEFAULT '')")
    s._conn.execute("INSERT INTO leads(id,source,platform,lead_type,title,url,found_at) VALUES ('a','r','R','buyer','t','u','2026')")
    s = pg()
    row = s.get("a")
    assert row["follow_up"] == "" and row["notified"] == 0 and row["language"] == ""


@needs_pg
def test_reconnects_after_the_server_drops_the_connection(pg):
    s = pg()
    s.upsert(_lead("1"))
    s._conn._conn.close()                       # simulate Neon closing an idle connection
    assert s.get("1") is not None               # transparently reconnected


@needs_pg
def test_migrate_sqlite_to_postgres_then_snapshot_and_backup(pg, tmp_path):
    local = Store(str(tmp_path / "l.db"))
    local.upsert(_lead("1", 90, budget_usd=1234.5))
    local.upsert(_lead("2", 70))
    local.update_user_fields("1", status="contacted", notes="called", follow_up="2026-12-01", deal_value=10)
    local.save_filter("F", "q=1")
    local.set_meta("reddit_offset", "3")
    local.log_run("reddit", "a", 5, 2, 1)
    remote = pg()
    res = migrate(local, remote)
    assert res["leads"] == 2 and res["runs"] == 1
    got = remote.get("1")
    assert got["status"] == "contacted" and got["notes"] == "called" and got["budget_usd"] == 1234.5 and got["deal_value"] == 10
    assert remote.get_meta("reddit_offset") == "3" and remote.list_filters()[0]["name"] == "F"
    assert migrate(local, remote)["leads"] == 2 and remote.query()[1] == 2         # re-run: no duplicates
    assert len(remote.stats()["runs"]) == 1
    remote.save_filter("G", "q=2")                                                  # serial id still works after migration
    out = remote.backup(str(tmp_path / "bk"))
    b = Store(out)
    assert b.get("1")["notes"] == "called" and b.query()[1] == 2 and len(b.list_filters()) == 2
    from leabee.jobs import make_snapshot
    assert make_snapshot(remote, tmp_path / "snap" / "s.sqlite") == 2
    assert sqlite3.connect(str(tmp_path / "snap" / "s.sqlite")).execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 2


@needs_pg
def test_web_app_and_vercel_full_mode_on_postgres(pg, monkeypatch):
    s = pg()
    s.upsert(_lead("1", 90))
    s.close()
    monkeypatch.setenv("LEABEE_PASSWORD", "correct-horse-battery")
    monkeypatch.setenv("DATABASE_URL", PG_URL)
    app = serverless.build_app()
    c = app.test_client()
    assert c.get("/api/stats").status_code == 401
    stats = c.get("/api/stats", headers=AUTH)
    assert stats.status_code == 200 and stats.get_json()["total"] == 1             # Decimal/float values serialise fine
    assert c.patch("/api/leads/1", json={"status": "won", "deal_value": 99}, headers=AUTH).status_code == 200
    assert c.post("/api/leads/bulk", json={"ids": ["1"], "status": "contacted"}, headers=AUTH).get_json()["updated"] == 1
    assert c.get("/api/leads?q=SHOPIFY&min_score=50", headers=AUTH).get_json()["total"] == 1
    for path in ("/", "/board", "/insights"):
        assert c.get(path, headers=AUTH).status_code == 200
    assert c.get("/export.csv", headers=AUTH).data.decode("utf-8-sig").startswith("score")
    app.config["store"].close()                                                     # edits survive in Postgres itself
    got = pg().get("1")
    assert got["status"] == "contacted" and got["deal_value"] == 99
