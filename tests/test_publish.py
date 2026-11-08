from types import SimpleNamespace

from leabee import jobs
from leabee.storage import Store

CFG = {"publish": {"enabled": True}}


def _store():
    s = Store(":memory:")
    s.upsert(dict(id="1", source="reddit", platform="Reddit", lead_type="buyer", title="a long enough lead title",
                  text="x", url="https://u/1", author="", author_url="", posted_at=None, categories="", country="",
                  budget_text="", budget_usd=None, contact="", score=70, reasons="", enriched=0))
    return s


def _fake_git(calls, staged_changes=True, push_ok=True):
    def git(*args, cwd=None):
        calls.append(args)
        rc = 0
        if args[0] == "diff":
            rc = 1 if staged_changes else 0
        if args[0] == "push" and not push_ok:
            rc = 1
        return SimpleNamespace(returncode=rc, stdout="https://github.com/me/priv.git\n", stderr="boom" if rc else "")
    return git


def test_disabled_by_default():
    assert jobs.publish_snapshot({}, _store())["skipped"] == "publish disabled"


def test_refuses_public_or_unverifiable_repo(monkeypatch):
    calls = []
    monkeypatch.setattr(jobs, "_git", _fake_git(calls))
    monkeypatch.setattr(jobs, "repo_is_public", lambda url: True)
    res = jobs.publish_snapshot(CFG, _store())
    assert res["published"] is False and "public" in res["error"]
    assert not any(c[0] in ("add", "commit", "push") for c in calls)      # nothing touched git
    monkeypatch.setattr(jobs, "repo_is_public", lambda url: None)         # non-GitHub / unknown
    assert jobs.publish_snapshot(CFG, _store())["published"] is False


def test_publishes_to_private_repo(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(jobs, "_git", _fake_git(calls))
    monkeypatch.setattr(jobs, "repo_is_public", lambda url: False)
    monkeypatch.setattr("leabee.config.ROOT", tmp_path)
    res = jobs.publish_snapshot(CFG, _store())
    assert res == {"published": True, "leads": 1}
    kinds = [c[0] for c in calls]
    assert kinds.index("add") < kinds.index("commit") < kinds.index("push")
    assert any(c[0] == "add" and "vercel_data/leads.sqlite" in c for c in calls)   # only the snapshot is staged
    assert (tmp_path / "vercel_data" / "leads.sqlite").exists()


def test_no_changes_and_push_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(jobs, "repo_is_public", lambda url: False)
    monkeypatch.setattr("leabee.config.ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(jobs, "_git", _fake_git(calls, staged_changes=False))
    assert jobs.publish_snapshot(CFG, _store())["skipped"] == "no changes"
    assert not any(c[0] == "push" for c in calls)
    monkeypatch.setattr(jobs, "_git", _fake_git([], push_ok=False))
    res = jobs.publish_snapshot(CFG, _store())
    assert res["published"] is False and res["committed"] is True and res["error"] == "boom"


def test_repo_is_public_parsing(monkeypatch):
    import requests

    monkeypatch.setattr(requests, "get", lambda url, **kw: SimpleNamespace(status_code=200 if url.endswith("/me/pub") else 404))
    assert jobs.repo_is_public("https://github.com/me/pub.git") is True
    assert jobs.repo_is_public("git@github.com:me/priv.git") is False
    assert jobs.repo_is_public("https://gitlab.com/me/x.git") is None
