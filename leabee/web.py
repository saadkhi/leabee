"""Flask dashboard + JSON API, hardened: auth with lockout, CSRF/origin checks, host allowlist, rate limits, CSP with nonce, strict validation."""
from __future__ import annotations

import json
import secrets
import threading
from datetime import datetime

from flask import Flask, Response, g, jsonify, render_template, request

from . import security as sec
from .classify import CATEGORY_PATTERNS
from .jobs import run_cycle
from .outreach import draft
from .pipeline import ALL_SOURCES
from .storage import STATUSES, Store

WRITE_METHODS = ("POST", "PATCH", "PUT", "DELETE")


def _loads(v):
    try:
        return json.loads(v) if v else {}
    except ValueError:
        return {}


def create_app(cfg: dict, store: Store, readonly: bool = False, remote_collect: bool = False) -> Flask:
    app = Flask(__name__, template_folder="templates")
    app.config["MAX_CONTENT_LENGTH"] = 1_000_000                 # 1 MB request bodies at most
    app.config["JSON_AS_ASCII"] = False
    app.config["PROPAGATE_EXCEPTIONS"] = False
    state = {"running": False, "log": [], "started": None}
    lock = threading.Lock()
    discover_lock = threading.Lock()                              # one business search at a time
    limiter = sec.RateLimiter()
    plain_pw, hashed_pw = sec.configured_secret()
    password_set = bool(plain_pw or hashed_pw)
    hosts = sec.allowed_hosts(password_set)

    def deny(msg: str, code: int, **headers):
        r = jsonify(error=msg) if request.path.startswith("/api/") else Response(msg, code, mimetype="text/plain")
        r.status_code = code
        for k, v in headers.items():
            r.headers[k.replace("_", "-")] = str(v)
        return r

    # ---------------------------------------------------------------- request guards (run in this order)
    @app.before_request
    def _guard():
        g.csp_nonce = secrets.token_urlsafe(16)
        ip = sec.client_ip(request)
        if not sec.host_ok(request.host, hosts):
            return deny("Host not allowed", 400)
        if password_set:                                           # authentication, with brute-force lockout
            if limiter.count(f"fail:{ip}", sec.LOGIN_FAILS[1]) >= sec.LOGIN_FAILS[0]:
                return deny("Too many failed logins. Try again later.", 429, Retry_After=sec.LOGIN_FAILS[1])
            a = request.authorization
            if not (a and sec.verify_password(a.password or "", plain_pw, hashed_pw)):
                if a:                                                              # a browser's first request has no credentials yet
                    limiter.add(f"fail:{ip}")
                return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="Leabee"'})
            limiter.reset(f"fail:{ip}")
        if readonly and request.method in WRITE_METHODS:
            return deny("Read-only deployment: edit leads locally, then run `leabee snapshot` and redeploy.", 403)
        if request.method in WRITE_METHODS:                         # CSRF: Basic auth is sent by the browser automatically
            if not sec.same_origin(request):
                return deny("Cross-site request blocked", 403)
            if request.method in ("POST", "PATCH", "PUT") and request.content_length and not request.is_json:
                return deny("Content-Type must be application/json", 415)
        ok, wait = limiter.allow(f"all:{ip}", *sec.GENERAL_LIMIT)
        if not ok:
            return deny("Too many requests", 429, Retry_After=wait)
        for prefix, (limit, window) in sec.HEAVY_LIMITS.items():
            if request.path == prefix and request.method == "POST":
                ok, wait = limiter.allow(f"{prefix}:{ip}", limit, window)
                if not ok:
                    return deny("Rate limit reached for this action. Try again later.", 429, Retry_After=wait)
        if request.path.endswith("/draft"):
            ok, wait = limiter.allow(f"draft:{ip}", *sec.DRAFT_LIMIT)
            if not ok:
                return deny("Too many drafts requested. Try again later.", 429, Retry_After=wait)
        return None

    @app.after_request
    def _headers(resp):
        nonce = getattr(g, "csp_nonce", "")
        h = resp.headers
        h["Content-Security-Policy"] = sec.csp(nonce)
        h["X-Content-Type-Options"] = "nosniff"
        h["X-Frame-Options"] = "DENY"
        h["Referrer-Policy"] = "no-referrer"
        h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        h["Cross-Origin-Opener-Policy"] = "same-origin"
        h["Cross-Origin-Resource-Policy"] = "same-origin"
        h["Cache-Control"] = "no-store"                             # lead data must never sit in shared caches
        if request.is_secure or request.headers.get("X-Forwarded-Proto") == "https":
            h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return resp

    @app.context_processor
    def _nonce():
        return {"csp_nonce": getattr(g, "csp_nonce", "")}

    for code, msg in ((400, "Bad request"), (404, "Not found"), (405, "Method not allowed"), (413, "Request too large"), (500, "Something went wrong")):
        app.register_error_handler(code, lambda e, code=code, msg=msg: deny(msg, code))      # never leak stack traces or internals

    # ---------------------------------------------------------------- input helpers
    def body() -> dict:
        b = request.get_json(silent=True)
        return b if isinstance(b, dict) else {}

    def _filters():
        a = request.args
        text = lambda k, n=80: str(a.get(k, ""))[:n]
        return dict(
            min_score=max(0, min(int(a.get("min_score", 0) or 0), 100)), status=text("status", 20), category=text("category", 40),
            country=text("country"), platform=text("platform"), lead_type=text("type", 20),
            q=text("q", 120).strip(), has_contact=a.get("contact") == "1",
            since_hours=max(0.0, min(float(a.get("since_hours", 0) or 0), 24 * 3650)), sort=text("sort", 20),
            language=text("language", 10), ai=text("ai", 10), follow_due=a.get("follow_due") == "1",
            grade=text("grade", 1), kind=text("kind", 20),
        )

    # ---------------------------------------------------------------- pages
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
            limit = max(1, min(int(request.args.get("limit", 100)), 500))
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
        b = body()
        msg = str(b.get("message", "")).strip()[:2000]
        if not msg:
            return jsonify(error="message required"), 400
        res = ask(msg, str(b.get("session_id", ""))[:80])
        return jsonify(res), (503 if res.get("error") else 200)

    @app.post("/api/discover")
    def discover():
        from .discovery import find_businesses, save_to_pipeline
        b = body()
        q = str(b.get("query", "")).strip()[:200]
        if not q:
            return jsonify(error="query required"), 400
        if not discover_lock.acquire(blocking=False):
            return jsonify(error="Another search is running. Try again in a moment."), 429
        try:
            n = int(b["n"]) if str(b.get("n", "")).isdigit() else None
            res = find_businesses(q, max(1, min(n, 50)) if n else None, cfg, store=store)
            if res.get("error"):
                return jsonify(res), 422
            if b.get("save") is True and not readonly:
                res["saved"] = save_to_pipeline(store, cfg, res["businesses"])
            return jsonify(res)
        finally:
            discover_lock.release()

    @app.get("/api/tools")
    def tools():
        from .extractors import status
        return jsonify(status())

    @app.get("/api/stats")
    def stats():
        return jsonify(store.stats())

    @app.get("/api/leads/<lead_id>/draft")
    def lead_draft(lead_id):
        lead = store.get(lead_id[:128])
        if not lead:
            return jsonify(error="not found"), 404
        return jsonify(draft(lead, cfg, use_ai=request.args.get("ai", "1") != "0"))

    @app.patch("/api/leads/<lead_id>")
    def patch_lead(lead_id):
        b = body()
        for k in ("status", "notes", "follow_up"):
            if k in b and b[k] is not None and not isinstance(b[k], str):
                return jsonify(error=f"{k} must be text"), 400
        if "deal_value" in b and b["deal_value"] is not None and (isinstance(b["deal_value"], bool) or not isinstance(b["deal_value"], (int, float))):
            return jsonify(error="deal_value must be a number"), 400
        kw = {k: b[k] for k in ("follow_up", "deal_value") if k in b}
        try:
            ok = store.update_user_fields(lead_id[:128], b.get("status"), b.get("notes"), **kw)
        except ValueError as e:
            return jsonify(error=str(e)), 400
        return (jsonify(ok=True), 200) if ok else (jsonify(error="not found"), 404)

    @app.post("/api/leads/bulk")
    def bulk():
        b = body()
        ids = [i[:128] for i in b.get("ids", []) if isinstance(i, str)][:2000] if isinstance(b.get("ids"), list) else []
        try:
            n = store.bulk_status(ids, b.get("status", "") if isinstance(b.get("status"), str) else "")
        except ValueError as e:
            return jsonify(error=str(e)), 400
        return jsonify(ok=True, updated=n)

    @app.get("/api/filters")
    def list_filters():
        return jsonify(store.list_filters())

    @app.post("/api/filters")
    def add_filter():
        b = body()
        try:
            store.save_filter(str(b.get("name", "")), str(b.get("query", "")))
        except ValueError as e:
            return jsonify(error=str(e)), 400
        return jsonify(ok=True), 201

    @app.delete("/api/filters/<int:fid>")
    def del_filter(fid):
        store.delete_filter(fid)
        return jsonify(ok=True)

    @app.get("/export.csv")
    def export():
        ids = [i[:128] for i in request.args.get("ids", "").split(",") if i][:5000] if "ids" in request.args else None
        rows, _ = store.query(limit=100_000, ids=ids, **_filters())
        return Response("\ufeff" + store.to_csv(rows), mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename=leads-{datetime.now():%Y%m%d-%H%M}.csv"})

    def _say(m):
        with lock:
            state["log"].append(sec.redact(m))

    def _job(sources, do_enrich):
        try:
            if run_cycle(cfg, store, sources, do_enrich=do_enrich, progress=_say) is None:
                _say("another collection is already running")
        finally:
            with lock:
                state["running"] = False

    @app.post("/api/collect")
    def collect():
        b = body()
        wanted = b.get("sources", ALL_SOURCES)
        sources = [s for s in wanted if s in ALL_SOURCES] if isinstance(wanted, list) else []
        sources = sources or ALL_SOURCES
        if remote_collect:
            from . import remote_collect as rc
            if rc.status()["running"]:
                return jsonify(error="a collection is already running"), 409
            ok, msg = rc.dispatch(sources, partial=bool(b.get("partial", True)), enrich=bool(b.get("enrich", True)))
            return (jsonify(ok=True, message=msg), 202) if ok else (jsonify(error=msg), 503)
        with lock:
            if state["running"]:
                return jsonify(error="a collection is already running"), 409
            state.update(running=True, log=[f"started: {', '.join(sources)}"], started=datetime.now().isoformat())
        threading.Thread(target=_job, args=(sources, bool(b.get("enrich", True))), daemon=True).start()
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
    app.config["limiter"] = limiter
    return app
