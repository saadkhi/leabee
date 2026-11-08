"""Flask dashboard + JSON API."""
from __future__ import annotations

import threading
from datetime import datetime

from flask import Flask, Response, jsonify, render_template, request

from .classify import CATEGORY_PATTERNS
from .pipeline import ALL_SOURCES, enrich, run_all
from .storage import STATUSES, Store


def create_app(cfg: dict, store: Store) -> Flask:
    app = Flask(__name__, template_folder="templates")
    state = {"running": False, "log": [], "started": None}
    lock = threading.Lock()

    def _filters():
        a = request.args
        return dict(
            min_score=int(a.get("min_score", 0) or 0), status=a.get("status", ""), category=a.get("category", ""),
            country=a.get("country", ""), platform=a.get("platform", ""), lead_type=a.get("type", ""),
            q=a.get("q", "").strip(), has_contact=a.get("contact") == "1",
            since_hours=float(a.get("since_hours", 0) or 0), sort=a.get("sort", "score"),
        )

    @app.get("/")
    def index():
        return render_template("index.html", categories=sorted(CATEGORY_PATTERNS), statuses=STATUSES,
                               sources=ALL_SOURCES, hot=cfg.get("hot_score", 70))

    @app.get("/api/leads")
    def leads():
        try:
            limit = min(int(request.args.get("limit", 100)), 500)
            offset = max(int(request.args.get("offset", 0)), 0)
            rows, total = store.query(limit=limit, offset=offset, **_filters())
        except ValueError:
            return jsonify(error="bad parameter"), 400
        for r in rows:
            r["categories"] = [c for c in (r["categories"] or "").split(",") if c]
            r["text"] = (r["text"] or "")[:1200]
        return jsonify(total=total, leads=rows)

    @app.get("/api/stats")
    def stats():
        return jsonify(store.stats())

    @app.patch("/api/leads/<lead_id>")
    def patch_lead(lead_id):
        body = request.get_json(silent=True) or {}
        try:
            ok = store.update_user_fields(lead_id, body.get("status"), body.get("notes"))
        except ValueError as e:
            return jsonify(error=str(e)), 400
        return (jsonify(ok=True), 200) if ok else (jsonify(error="not found"), 404)

    @app.get("/export.csv")
    def export():
        rows, _ = store.query(limit=10 ** 6, **_filters())
        return Response("﻿" + store.to_csv(rows), mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename=leads-{datetime.now():%Y%m%d-%H%M}.csv"})

    def _job(sources, do_enrich):
        def say(m):
            with lock:
                state["log"].append(m)
        try:
            run_all(cfg, store, sources, progress=say)
            if do_enrich:
                say(f"enrich: {enrich(cfg, store, 100)}")
            say("done")
        except Exception as e:  # pragma: no cover
            say(f"ERROR {e}")
        finally:
            with lock:
                state["running"] = False

    @app.post("/api/collect")
    def collect():
        body = request.get_json(silent=True) or {}
        sources = [s for s in body.get("sources", ALL_SOURCES) if s in ALL_SOURCES] or ALL_SOURCES
        with lock:
            if state["running"]:
                return jsonify(error="a collection is already running"), 409
            state.update(running=True, log=[f"started: {', '.join(sources)}"], started=datetime.now().isoformat())
        threading.Thread(target=_job, args=(sources, bool(body.get("enrich", True))), daemon=True).start()
        return jsonify(ok=True), 202

    @app.get("/api/collect/status")
    def collect_status():
        with lock:
            return jsonify(running=state["running"], log=list(state["log"]), started=state["started"])

    return app
