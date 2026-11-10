"""Vercel (serverless) entrypoint.

Two modes, chosen by environment variables:

* **Full web mode** (recommended): TURSO_DATABASE_URL + TURSO_AUTH_TOKEN are set. Everything works from the browser:
  edit leads, board, drafts, saved filters. The Collect button starts a GitHub Actions run
  (needs GITHUB_DISPATCH_TOKEN + GITHUB_REPO) that writes into the same Turso database.
* **Snapshot mode** (fallback): no Turso variables; serves a read-only copy of vercel_data/leads.sqlite.

Either way LEABEE_PASSWORD is required: without it the site refuses to serve, because leads contain public-post
authors and contact details.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from flask import Flask, Response

from .config import ROOT, load_config
from .storage import Store

SNAPSHOT = ROOT / "vercel_data" / "leads.sqlite"
TMP_DB = Path(os.environ.get("TMPDIR", "/tmp")) / "leabee-leads.db"


def _message_app(text: str, status: int = 503) -> Flask:
    app = Flask(__name__)

    @app.route("/", defaults={"p": ""})
    @app.route("/<path:p>")
    def _msg(p):
        return Response(text, status, mimetype="text/plain")
    return app


def build_app() -> Flask:
    from .web import create_app

    if not os.environ.get("LEABEE_PASSWORD"):
        return _message_app("Set LEABEE_PASSWORD in the Vercel project's Environment Variables, then redeploy. "
                            "Leabee will not serve lead data without a password.")
    cfg = load_config()

    if os.environ.get("TURSO_DATABASE_URL"):
        try:
            store = Store(os.environ["TURSO_DATABASE_URL"].strip())
        except Exception as e:      # wrong URL/token, Turso down: say so without leaking secrets
            return _message_app(f"Could not open the Turso database ({e.__class__.__name__}: {str(e)[:200]}). "
                                "Check TURSO_DATABASE_URL and TURSO_AUTH_TOKEN.")
        return create_app(cfg, store, readonly=False, remote_collect=True)

    if not SNAPSHOT.exists():
        return _message_app("No database configured. Set TURSO_DATABASE_URL and TURSO_AUTH_TOKEN (full mode), or run "
                            "`python -m leabee snapshot`, commit vercel_data/leads.sqlite and redeploy (read-only mode).")
    if not TMP_DB.exists():
        shutil.copyfile(SNAPSHOT, TMP_DB)       # the bundle is read-only; SQLite needs a writable file
    cfg["database"] = str(TMP_DB)
    return create_app(cfg, Store(str(TMP_DB)), readonly=True)


app = build_app()
