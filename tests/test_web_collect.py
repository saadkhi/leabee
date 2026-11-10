from types import SimpleNamespace

import requests

from leabee import remote_collect as rc
from leabee.http import Http
from leabee.storage import Store
from leabee.web import create_app

CFG = {"min_score": 35, "max_age_days": 10 ** 5, "hot_score": 70}


def test_collect_button_dispatches_github_actions(monkeypatch):
    app = create_app(CFG, Store(":memory:"), remote_collect=True).test_client()
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


def test_dispatch_reports_github_errors(monkeypatch):
    monkeypatch.setenv("GITHUB_DISPATCH_TOKEN", "bad")
    monkeypatch.setenv("GITHUB_REPO", "me/leabee")
    monkeypatch.setattr(requests, "post", lambda *a, **k: SimpleNamespace(status_code=403, text="Resource not accessible"))
    ok, msg = rc.dispatch(["reddit"])
    assert ok is False and "403" in msg


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
