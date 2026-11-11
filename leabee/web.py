"""Flask dashboard + JSON API."""
from __future__ import annotations

import hmac
import json
import os
import threading
from datetime import datetime

from flask import Flask, Response, jsonify, render_template, request

from .classify import CATEGORY_PATTERNS
from .jobs import run_cycle
from .outreach import draft
from .pipeline import ALL_SOURCES
from .storage import STATUSES, Store


def _loads(v):
    try:
        return json.loads(v) if v else {}
    except ValueError:
        return {}


def create_app(cfg: dict, store: Store, readonly: bool = False, remote_collect: bool = False) -> Flask:
    app = Flask(__name__, template_folder="templates")
    state = {"running": False, "log": [], "started": None}
    lock = threading.Lock()
    password = os.environ.get("LEABEE_PASSWORD", "")

    @app.before_request
    def _readonly():
        """Serverless (Vercel) mode serves a snapshot: block anything that would write."""
        if readonly and (request.method in ("POST", "PATCH", "PUT", "DELETE")):
            return jsonify(error="Read-only deployment: edit leads locally, then run `leabee snapshot` and redeploy."), 403
        return None

    @app.before_request
    def _auth():
        """Optional HTTP Basic auth: set LEABEE_PASSWORD (any username) before exposing this online."""
        if not password:
            return None
        a = request.authorization
        if a and hmac.compare_digest(a.password or "", password):
            return None
        return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="Leabee"'})

    def _filters():
        a = request.args
        return dict(
            min_score=int(a.get("min_score", 0) or 0), status=a.get("status", ""), category=a.get("category", ""),
            country=a.get("country", ""), platform=a.get("platform", ""), lead_type=a.get("type", ""),
            q=a.get("q", "").strip(), has_contact=a.get("contact") == "1",
            since_hours=float(a.get("since_hours", 0) or 0), sort=a.get("sort", "score"),
            language=a.get("language", ""), ai=a.get("ai", ""), follow_due=a.get("follow_due") == "1",
            grade=a.get("grade", ""), kind=a.get("kind", ""),
        )

    @app.get("/")
    def index():
        return render_template("index.html", categories=sorted(CATEGORY_PATTERNS), statuses=STATUSES,
                               sources=ALL_SOURCES, hot=cfg.get("hot_score", 70), readonly=readonly,
                               remote_collect=remote_collect)

    @app.get("/insights")
    def insights():
        return render_template("insights.html")

    @app.get("/board")
    def board():
        return render_template("board.html", statuses=[s for s in STATUSES if s != "ignored"])

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
            r["details"] = _loads(r.get("details"))
            r["components"] = _loads(r.get("components"))
            r["reasons"] = [x for x in (r.get("reasons") or "").split(" | ") if x]
        return jsonify(total=total, leads=rows)

    @app.post("/api/copilot/ask")
    def copilot_ask():
        from .copilot.client import ask
        body = request.get_json(silent=True) or {}
        msg = str(body.get("message", "")).strip()
        if not msg:
            return jsonify(error="message required"), 400
        res = ask(msg, str(body.get("session_id", "")))
        return jsonify(res), (503 if res.get("error") else 200)

    @app.post("/api/discover")
    def discover():
        from .discovery import find_businesses, save_to_pipeline
        body = request.get_json(silent=True) or {}
        q = str(body.get("query", "")).strip()
        if not q:
            return jsonify(error="query required"), 400
        res = find_businesses(q, int(body["n"]) if str(body.get("n", "")).isdigit() else None, cfg, store=store)
        if res.get("error"):
            return jsonify(res), 422
        if body.get("save") and not readonly:
            res["saved"] = save_to_pipeline(store, cfg, res["businesses"])
        return jsonify(res)

    @app.get("/api/tools")
    def tools():
        from .extractors import status
        return jsonify(status())

    @app.get("/api/stats")
    def stats():
        return jsonify(store.stats())

    @app.get("/api/leads/<lead_id>/draft")
    def lead_draft(lead_id):
        lead = store.get(lead_id)
        if not lead:
            return jsonify(error="not found"), 404
        return jsonify(draft(lead, cfg, use_ai=request.args.get("ai", "1") != "0"))

    @app.patch("/api/leads/<lead_id>")
    def patch_lead(lead_id):
        body = request.get_json(silent=True) or {}
        kw = {k: body[k] for k in ("follow_up", "deal_value") if k in body}
        try:
            ok = store.update_user_fields(lead_id, body.get("status"), body.get("notes"), **kw)
        except ValueError as e:
            return jsonify(error=str(e)), 400
        return (jsonify(ok=True), 200) if ok else (jsonify(error="not found"), 404)

    @app.post("/api/leads/bulk")
    def bulk():
        body = request.get_json(silent=True) or {}
        ids = [i for i in body.get("ids", []) if isinstance(i, str)][:2000]
        try:
            n = store.bulk_status(ids, body.get("status", ""))
        except ValueError as e:
            return jsonify(error=str(e)), 400
        return jsonify(ok=True, updated=n)

    @app.get("/api/filters")
    def list_filters():
        return jsonify(store.list_filters())

    @app.post("/api/filters")
    def add_filter():
        body = request.get_json(silent=True) or {}
        try:
            store.save_filter(str(body.get("name", "")), str(body.get("query", "")))
        except ValueError as e:
            return jsonify(error=str(e)), 400
        return jsonify(ok=True), 201

    @app.delete("/api/filters/<int:fid>")
    def del_filter(fid):
        store.delete_filter(fid)
        return jsonify(ok=True)

    @app.get("/export.csv")
    def export():
        ids = [i for i in request.args.get("ids", "").split(",") if i] if "ids" in request.args else None
        rows, _ = store.query(limit=10 ** 6, ids=ids, **_filters())
        return Response("﻿" + store.to_csv(rows), mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename=leads-{datetime.now():%Y%m%d-%H%M}.csv"})

    def _say(m):
        with lock:
            state["log"].append(m)

    def _job(sources, do_enrich):
        try:
            if run_cycle(cfg, store, sources, do_enrich=do_enrich, progress=_say) is None:
                _say("another collection is already running")
        finally:
            with lock:
                state["running"] = False

    @app.post("/api/collect")
    def collect():
        body = request.get_json(silent=True) or {}
        sources = [s for s in body.get("sources", ALL_SOURCES) if s in ALL_SOURCES] or ALL_SOURCES
        if remote_collect:
            from . import remote_collect as rc
            if rc.status()["running"]:
                return jsonify(error="a collection is already running"), 409
            ok, msg = rc.dispatch(sources, partial=bool(body.get("partial", True)), enrich=bool(body.get("enrich", True)))
            return (jsonify(ok=True, message=msg), 202) if ok else (jsonify(error=msg), 503)
        with lock:
            if state["running"]:
                return jsonify(error="a collection is already running"), 409
            state.update(running=True, log=[f"started: {', '.join(sources)}"], started=datetime.now().isoformat())
        threading.Thread(target=_job, args=(sources, bool(body.get("enrich", True))), daemon=True).start()
        return jsonify(ok=True), 202

    @app.get("/api/collect/status")
    def collect_status():
        if remote_collect:
            from . import remote_collect as rc
            return jsonify(rc.status())
        with lock:
            return jsonify(running=state["running"], log=list(state["log"]), started=state["started"])

    app.config["state_say"] = _say
    app.config["store"] = store
    return app
