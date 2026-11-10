"""Google Maps leads through the omkarcloud "Google Maps Extractor" (https://github.com/omkarcloud/google-maps-scraper).

What that tool is: a DESKTOP APP (built on Botasaurus) that you download from omkar.cloud and sign in to (free tier: 200 searches
a month). It exposes a local REST API (default http://localhost:8000, routes /api/tasks/...). It is not a pip library and the
GitHub repository contains only documentation, so Leabee talks to the running app, or reads its CSV/JSON exports:

  1. API mode    config `gmaps.api_url` + `gmaps.searches`: Leabee creates one task per search, waits, reads results.
  2. Import mode drop exports into data/maps/ (or run `python -m leabee import-maps FILE`); they are picked up on every run.

Heads-up: scraping Google Maps is against Google's terms of service and cold outreach is regulated (GDPR / CAN-SPAM / CASL).
Use it for research and personal, relevant, opt-out-able outreach, and keep volumes modest.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Iterator

import requests

from ..models import RawItem
from ..prospects import read_export, record_to_item

log = logging.getLogger("leabee.gmaps")
DONE = {"completed", "complete", "done", "success", "finished"}
FAILED = {"failed", "error", "aborted", "cancelled", "canceled"}


class BotasaurusApi:
    """Minimal client for the app's local API (same routes as the official `botasaurus-api` package)."""

    def __init__(self, url: str = "http://localhost:8000", timeout: float = 60):
        self.url, self.timeout = url.rstrip("/"), timeout

    def alive(self) -> bool:
        try:
            return requests.get(f"{self.url}/api", timeout=5).status_code < 500
        except requests.RequestException:
            return False

    def create_task(self, data: dict, scraper_name: str | None = None) -> dict:
        r = requests.post(f"{self.url}/api/tasks/create-task-async", json={"data": data, "scraper_name": scraper_name}, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def get_task(self, task_id) -> dict:
        r = requests.get(f"{self.url}/api/tasks/{task_id}", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def results(self, task_id, per_page: int = 500) -> Iterator[dict]:
        page = 1
        while True:
            r = requests.post(f"{self.url}/api/tasks/{task_id}/results", json={"page": page, "per_page": per_page}, timeout=self.timeout * 2)
            r.raise_for_status()
            body = r.json()
            rows = body.get("results") if isinstance(body, dict) else body
            yield from (rows or [])
            if not isinstance(body, dict) or page >= int(body.get("total_pages", 1) or 1):
                return
            page += 1


def _template(tpl: dict, query: str) -> dict:
    """Fill the `{query}` placeholder anywhere in the task template."""
    def fill(v):
        if isinstance(v, str):
            return v.replace("{query}", query)
        if isinstance(v, list):
            return [fill(x) for x in v]
        if isinstance(v, dict):
            return {k: fill(x) for k, x in v.items()}
        return v
    return fill(tpl)


def collect(cfg: dict, http=None) -> Iterator[RawItem]:
    g = cfg.get("gmaps") or {}
    seen: set[str] = set()

    def emit(rec: dict):
        item = record_to_item(rec)
        if item and item.url not in seen:
            seen.add(item.url)
            return item
        return None

    # 1) exports dropped into data/maps/
    folder = Path(g.get("import_dir") or (Path(cfg["database"]).parent / "maps"))
    if folder.is_dir():
        for f in sorted(folder.glob("*")):
            if f.suffix.lower() in (".csv", ".json", ".jsonl"):
                try:
                    for rec in read_export(f):
                        it = emit(rec)
                        if it:
                            yield it
                except (OSError, ValueError) as e:
                    log.warning("could not read %s: %s", f, e)

    # 2) live API
    searches = g.get("searches") or []
    if not searches:
        return
    api = BotasaurusApi(g.get("api_url", "http://localhost:8000"))
    if not api.alive():
        log.warning("Google Maps Extractor app is not running at %s (start the desktop app first); skipped API searches", api.url)
        return
    tpl = g.get("task_template") or {"queries": ["{query}"], "max_results": int(g.get("max_results_per_search", 120))}
    for q in searches:
        try:
            task = api.create_task(_template(tpl, q), g.get("scraper_name"))
            tid = task.get("id") or (task.get("task") or {}).get("id")
            deadline = time.time() + float(g.get("task_timeout_s", 900))
            status = ""
            while time.time() < deadline:
                status = str(api.get_task(tid).get("status", "")).lower()
                if status in DONE or status in FAILED:
                    break
                time.sleep(5)
            if status not in DONE:
                log.warning("maps search %r ended with status %r", q, status or "timeout")
                continue
            for rec in api.results(tid):
                it = emit(rec)
                if it:
                    yield it
        except (requests.RequestException, ValueError, KeyError) as e:
            log.warning("maps search %r failed: %s", q, e)
