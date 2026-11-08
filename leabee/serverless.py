"""Vercel (serverless) entrypoint.

Vercel functions have a read-only filesystem (except /tmp), no background threads and short time limits, so on Vercel
Leabee runs as a **read-only dashboard over a database snapshot** (vercel_data/leads.sqlite, built by
`python -m leabee snapshot`). Collection happens elsewhere (your PC or the GitHub Actions workflow) and a redeploy
publishes the fresh snapshot. The dashboard refuses to serve unless LEABEE_PASSWORD is set, because the leads
contain public-post authors and contact details.
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


def build_app() -> Flask:
    from .web import create_app

    if not os.environ.get("LEABEE_PASSWORD"):
        app = Flask(__name__)

        @app.route("/", defaults={"p": ""})
        @app.route("/<path:p>")
        def _locked(p):
            return Response("Set LEABEE_PASSWORD in the Vercel project's Environment Variables, then redeploy. "
                            "Leabee will not serve lead data without a password.", 503, mimetype="text/plain")
        return app

    cfg = load_config()
    if not SNAPSHOT.exists():
        app = Flask(__name__)

        @app.route("/", defaults={"p": ""})
        @app.route("/<path:p>")
        def _nosnap(p):
            return Response("No snapshot found. Run `python -m leabee snapshot`, commit vercel_data/leads.sqlite and redeploy.",
                            503, mimetype="text/plain")
        return app

    if not TMP_DB.exists():
        shutil.copyfile(SNAPSHOT, TMP_DB)       # the bundle is read-only; SQLite needs a writable file
    cfg["database"] = str(TMP_DB)
    return create_app(cfg, Store(str(TMP_DB)), readonly=True)


app = build_app()
