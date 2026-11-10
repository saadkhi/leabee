"""Vercel (serverless) entrypoint.

Two modes, chosen by environment variables:

* **Full web mode** (recommended): DATABASE_URL (a Neon Postgres connection string) is set. Everything works from the browser:
  edit leads, board, drafts, saved filters. The Collect button starts a GitHub Actions run
  (needs GITHUB_DISPATCH_TOKEN + GITHUB_REPO) that writes into the same Neon database.
* **Snapshot mode** (fallback): no DATABASE_URL; serves a read-only copy of vercel_data/leads.sqlite.

Either way LEABEE_PASSWORD is required: without it the site refuses to serve, because leads contain public-post
authors and contact details.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from flask import Flask, Response

from .config import ROOT, load_config
from .storage import Store, database_url

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

    url = database_url()
    if url:
        try:
            store = Store(url)
        except Exception as e:      # wrong URL/password, Neon unreachable: say so without leaking the connection string
            msg = str(e).replace(url, "<DATABASE_URL>")[:200]
            return _message_app(f"Could not open the Neon database ({e.__class__.__name__}: {msg}). "
                                "Check the DATABASE_URL environment variable (use Neon's pooled connection string).")
        return create_app(cfg, store, readonly=False, remote_collect=True)

    if not SNAPSHOT.exists():
        return _message_app("No database configured. Set DATABASE_URL to your Neon connection string (full mode), or run "
                            "`python -m leabee snapshot`, commit vercel_data/leads.sqlite and redeploy (read-only mode).")
    if not TMP_DB.exists():
        shutil.copyfile(SNAPSHOT, TMP_DB)       # the bundle is read-only; SQLite needs a writable file
    cfg["database"] = str(TMP_DB)
    return create_app(cfg, Store(str(TMP_DB)), readonly=True)


app = build_app()
