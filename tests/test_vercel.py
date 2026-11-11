import base64
import importlib
import sqlite3

from leabee import serverless
from leabee.jobs import make_snapshot
from leabee.storage import Store
from leabee.web import create_app

CFG = {"min_score": 35, "max_age_days": 10 ** 5, "hot_score": 70}
AUTH = {"Authorization": "Basic " + base64.b64encode(b"me:correct-horse-battery").decode()}


def _lead(i, score=60, **kw):
    d = dict(id=i, source="reddit", platform="Reddit", lead_type="buyer", title=f"lead title number {i}", text="x",
             url=f"https://u/{i}", author="", author_url="", posted_at="2026-11-08T00:00:00+00:00", categories="automation",
             country="India", budget_text="", budget_usd=None, contact="", score=score, reasons="", enriched=0, language="en")
    d.update(kw)
    return d


def test_snapshot_is_single_compact_file(tmp_path):
    s = Store(str(tmp_path / "work.db"))
    s.upsert(_lead("1", 90)); s.upsert(_lead("2", 40)); s.upsert(_lead("3", 80))
    s.update_user_fields("3", status="ignored")
    out = tmp_path / "vercel_data" / "leads.sqlite"
    assert make_snapshot(s, out, min_score=50) == 1                 # ignored + low score removed
    assert sorted(p.name for p in out.parent.iterdir()) == ["leads.sqlite"]   # no -wal/-shm/.tmp left behind
    assert sqlite3.connect(str(out)).execute("SELECT id FROM leads").fetchall() == [("1",)]


def test_readonly_blocks_writes_but_serves_reads():
    s = Store(":memory:")
    s.upsert(_lead("1", 90))
    c = create_app(CFG, s, readonly=True).test_client()
    assert c.get("/").status_code == 200 and b"Read-only snapshot" in c.get("/").data
    assert c.get("/api/leads").get_json()["total"] == 1
    assert c.patch("/api/leads/1", json={"status": "won"}).status_code == 403
    assert c.post("/api/leads/bulk", json={"ids": ["1"], "status": "won"}).status_code == 403
    assert c.post("/api/collect", json={}).status_code == 403
    assert c.post("/api/filters", json={"name": "a", "query": "b"}).status_code == 403
    assert s.get("1")["status"] == "new"


def test_vercel_entrypoint_refuses_without_password(monkeypatch):
    monkeypatch.delenv("LEABEE_PASSWORD", raising=False)
    app = serverless.build_app().test_client()
    r = app.get("/api/leads")
    assert r.status_code == 503 and b"LEABEE_PASSWORD" in r.data


def test_vercel_entrypoint_serves_snapshot_with_password(monkeypatch, tmp_path):
    s = Store(str(tmp_path / "work.db"))
    s.upsert(_lead("1", 90))
    snap = tmp_path / "snap" / "leads.sqlite"
    make_snapshot(s, snap)
    monkeypatch.setenv("LEABEE_PASSWORD", "correct-horse-battery")
    monkeypatch.setenv("LEABEE_ALLOW_BASIC", "1")
    monkeypatch.setattr(serverless, "SNAPSHOT", snap)
    monkeypatch.setattr(serverless, "TMP_DB", tmp_path / "tmp-leads.db")
    c = serverless.build_app().test_client()
    assert c.get("/api/stats").status_code == 401
    assert c.get("/api/stats", headers=AUTH).get_json()["total"] == 1
    assert c.patch("/api/leads/1", json={"status": "won"}, headers=AUTH).status_code == 403


def test_vercel_entrypoint_without_snapshot(monkeypatch, tmp_path):
    monkeypatch.setenv("LEABEE_PASSWORD", "correct-horse-battery")
    monkeypatch.setenv("LEABEE_ALLOW_BASIC", "1")
    monkeypatch.setattr(serverless, "SNAPSHOT", tmp_path / "missing.sqlite")
    r = serverless.build_app().test_client().get("/")
    assert r.status_code == 503 and b"snapshot" in r.data


def test_db_env_override(monkeypatch, tmp_path):
    from leabee.config import load_config
    monkeypatch.setenv("LEABEE_DB", str(tmp_path / "x.db"))
    assert load_config()["database"] == str(tmp_path / "x.db")
