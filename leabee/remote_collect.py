"""Run collection on GitHub Actions from the hosted dashboard (Vercel functions are too short for a full run).

Needs on the Vercel project:  GITHUB_DISPATCH_TOKEN  (fine-grained token, repo "Actions: read and write")
                              GITHUB_REPO            (owner/name, e.g. saadkhi/leabee; auto-detected for git-connected deploys)
"""
from __future__ import annotations

import os

import requests

WORKFLOW = "refresh.yml"
API = "https://api.github.com"


def repo() -> str:
    r = os.environ.get("GITHUB_REPO", "").strip()
    if r:
        return r
    owner, slug = os.environ.get("VERCEL_GIT_REPO_OWNER", ""), os.environ.get("VERCEL_GIT_REPO_SLUG", "")
    return f"{owner}/{slug}" if owner and slug else ""


def _headers() -> dict:
    return {"Authorization": f"Bearer {os.environ.get('GITHUB_DISPATCH_TOKEN', '').strip()}",
            "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


def configured() -> bool:
    return bool(os.environ.get("GITHUB_DISPATCH_TOKEN", "").strip() and repo())


def setup_hint() -> str:
    return ("Set GITHUB_DISPATCH_TOKEN (a fine-grained GitHub token with Actions read/write on this repo) and GITHUB_REPO "
            "(owner/name) in the Vercel environment variables, then redeploy.")


def dispatch(sources: list[str], partial: bool = True, enrich: bool = True) -> tuple[bool, str]:
    if not configured():
        return False, setup_hint()
    try:
        r = requests.post(f"{API}/repos/{repo()}/actions/workflows/{WORKFLOW}/dispatches", headers=_headers(), timeout=20,
                          json={"ref": os.environ.get("GITHUB_BRANCH", "main"),
                                "inputs": {"sources": ",".join(sources), "partial": str(partial).lower(),
                                           "enrich": str(enrich).lower()}})
    except requests.RequestException as e:
        return False, f"could not reach GitHub: {e.__class__.__name__}"
    if r.status_code == 204:
        return True, "Collection started on GitHub Actions."
    return False, f"GitHub refused the request ({r.status_code}): {r.text[:200]}"


def status() -> dict:
    """Shape matches the local collect status: {running, log, started}."""
    if not configured():
        return {"running": False, "log": [], "started": None}
    try:
        r = requests.get(f"{API}/repos/{repo()}/actions/workflows/{WORKFLOW}/runs?per_page=1", headers=_headers(), timeout=20)
        runs = r.json().get("workflow_runs", []) if r.status_code == 200 else []
    except (requests.RequestException, ValueError):
        return {"running": False, "log": ["could not read GitHub Actions status"], "started": None}
    if not runs:
        return {"running": False, "log": [], "started": None}
    run = runs[0]
    running = run.get("status") in ("queued", "in_progress", "waiting", "requested", "pending")
    line = f"GitHub Actions run #{run.get('run_number')}: {run.get('status')}"
    if run.get("conclusion"):
        line += f" ({run['conclusion']})"
    return {"running": running, "log": [line, run.get("html_url", "")], "started": run.get("created_at")}
