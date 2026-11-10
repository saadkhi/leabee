"""One collection cycle (collect -> enrich -> AI review -> alerts -> backup) and a background scheduler."""
from __future__ import annotations

import logging
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from .storage import Store

log = logging.getLogger("leabee.jobs")
CYCLE_LOCK = threading.Lock()


def backup_dir(cfg: dict) -> str:
    d = (cfg.get("backup", {}) or {}).get("dir", "data/backups")
    p = Path(d)
    return str(p if p.is_absolute() else Path(cfg["database"]).parent.parent / p)


def run_cycle(cfg: dict, store: Store, sources: list[str] | None = None, partial: bool = False,
              do_enrich: bool = True, progress: Callable[[str], None] | None = None) -> dict | None:
    """Returns None if another cycle is already running."""
    from . import ai, notify
    from .pipeline import enrich, run_all

    if not CYCLE_LOCK.acquire(blocking=False):
        return None
    say = progress or (lambda m: None)
    out: dict = {}
    try:
        res = run_all(cfg, store, sources, progress=say, partial=partial)
        out["collect"] = res
        if do_enrich:
            out["enrich"] = enrich(cfg, store, int((cfg.get("tinyfish", {}) or {}).get("enrich_per_run", 100)))
            say(f"enrich: {out['enrich']}")
        out["ai"] = ai.review(cfg, store, progress=say)
        if out["ai"].get("reviewed"):
            say(f"ai: {out['ai']}")
        out["alerts"] = notify.alert_new_hot(cfg, store)
        if out["alerts"].get("sent"):
            say(f"alerts: {out['alerts']}")
        pub = publish_snapshot(cfg, store)
        out["publish"] = pub
        if pub.get("published") or pub.get("error"):
            say(f"publish: {pub}")
        say("done")
        return out
    except Exception as e:  # pragma: no cover
        log.exception("cycle failed")
        say(f"ERROR {e}")
        out["error"] = str(e)
        return out
    finally:
        CYCLE_LOCK.release()


def make_snapshot(store: Store, out: Path, min_score: int = 0) -> int:
    """Compact, single-file copy of the database for read-only hosting (no WAL files, ignored leads removed)."""
    import sqlite3

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    with store._lock:
        dst = sqlite3.connect(str(tmp))
        try:
            store._copy_to(dst)
            dst.execute("DELETE FROM leads WHERE status='ignored' OR score<?", (min_score,))
            dst.execute("DELETE FROM runs WHERE id NOT IN (SELECT id FROM runs ORDER BY id DESC LIMIT 50)")
            dst.commit()
            dst.execute("PRAGMA journal_mode=DELETE")
            dst.execute("VACUUM")
            n = dst.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        finally:
            dst.close()
    out.unlink(missing_ok=True)
    tmp.replace(out)
    return n


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=120)


def repo_is_public(remote_url: str) -> bool | None:
    """True/False if GitHub says the repo is public/not-public (unauthenticated check), None if not a GitHub URL."""
    import re

    import requests

    m = re.search(r"github\.com[:/]+([^/]+)/([^/.]+?)(?:\.git)?/?$", remote_url.strip())
    if not m:
        return None
    try:
        r = requests.get(f"https://api.github.com/repos/{m.group(1)}/{m.group(2)}", timeout=15,
                         headers={"Accept": "application/vnd.github+json"})   # deliberately no token
    except requests.RequestException:
        return True                     # cannot verify -> treat as public (fail safe)
    return r.status_code == 200


def publish_snapshot(cfg: dict, store: Store, force: bool = False) -> dict:
    """Snapshot -> git commit -> push, so a connected Vercel project redeploys.

    Only vercel_data/leads.sqlite is committed. Refuses unless the GitHub repo is private, because the snapshot
    holds lead data (post authors, contact details)."""
    from .config import ROOT

    pcfg = cfg.get("publish", {}) or {}
    if not (force or pcfg.get("enabled")):
        return {"published": False, "skipped": "publish disabled"}
    remote, branch = pcfg.get("remote", "origin"), pcfg.get("branch", "main")
    url = _git("remote", "get-url", remote, cwd=ROOT)
    if url.returncode != 0:
        return {"published": False, "error": f"no git remote '{remote}'"}
    if not pcfg.get("allow_public"):
        pub = repo_is_public(url.stdout)
        if pub is not False:
            return {"published": False, "error": "refusing to publish lead data: the GitHub repo is public (or could not be "
                    "verified private). Make it private, or set publish.allow_public: true if you really mean it."}
    out = ROOT / "vercel_data" / "leads.sqlite"
    n = make_snapshot(store, out, int(pcfg.get("min_score", 0)))
    _git("add", "-f", "vercel_data/leads.sqlite", cwd=ROOT)
    if _git("diff", "--cached", "--quiet", "--", "vercel_data/leads.sqlite", cwd=ROOT).returncode == 0:
        return {"published": False, "leads": n, "skipped": "no changes"}
    c = _git("commit", "-m", f"chore: refresh lead snapshot ({n} leads)", "--", "vercel_data/leads.sqlite", cwd=ROOT)
    if c.returncode != 0:
        return {"published": False, "error": (c.stderr or c.stdout).strip()[:300]}
    p = _git("push", remote, f"HEAD:{branch}", cwd=ROOT)
    if p.returncode != 0:
        return {"published": False, "committed": True, "error": p.stderr.strip()[:300]}
    return {"published": True, "leads": n}


def maybe_backup(cfg: dict, store: Store) -> str | None:
    """At most one backup per calendar day."""
    bcfg = cfg.get("backup", {}) or {}
    if bcfg.get("enabled") is False or getattr(store, "remote", False):
        return None             # Neon keeps its own copy; use `python -m leabee backup` to pull a local one
    today = datetime.now().strftime("%Y-%m-%d")
    if store.get_meta("last_backup") == today:
        return None
    path = store.backup(backup_dir(cfg), int(bcfg.get("keep", 14)))
    if path:
        store.set_meta("last_backup", today)
    return path


class Scheduler(threading.Thread):
    """Runs a cycle every `every_minutes` while the server is up."""

    def __init__(self, cfg: dict, store: Store, every_minutes: float, partial: bool = True,
                 log_sink: Callable[[str], None] | None = None):
        super().__init__(daemon=True, name="leabee-scheduler")
        self.cfg, self.store, self.every = cfg, store, max(every_minutes, 5) * 60
        self.partial, self.sink = partial, log_sink
        self._stop_evt = threading.Event()

    def stop(self) -> None:
        self._stop_evt.set()

    def run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                run_cycle(self.cfg, self.store, partial=self.partial, progress=self.sink)
                maybe_backup(self.cfg, self.store)
            except Exception:  # pragma: no cover
                log.exception("scheduled cycle failed")
            self._stop_evt.wait(self.every)
