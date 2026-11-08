"""One collection cycle (collect -> enrich -> AI review -> alerts -> backup) and a background scheduler."""
from __future__ import annotations

import logging
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
            store._conn.backup(dst)
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


def maybe_backup(cfg: dict, store: Store) -> str | None:
    """At most one backup per calendar day."""
    bcfg = cfg.get("backup", {}) or {}
    if bcfg.get("enabled") is False:
        return None
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
