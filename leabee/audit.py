"""`python -m leabee audit`: a quick security check of YOUR setup (not a penetration test).

Levels: PASS, WARN (should fix), FAIL (fix before exposing the app). Exit code 1 when anything FAILs."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from . import security as sec
from .config import ROOT

TEXT_EXT = {".py", ".yaml", ".yml", ".json", ".md", ".txt", ".html", ".js", ".toml", ".cfg", ".ini", ".env", ".example", ".sh", ".bat", ".csv"}


def _git(*args: str) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True, timeout=60)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def scan_staged() -> list[tuple[str, str]]:
    """Secrets in the lines about to be committed (used by the pre-commit hook)."""
    out = []
    diff = _git("diff", "--cached", "-U0", "--no-color")
    current = ""
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
        elif line.startswith("+") and not line.startswith("+++"):
            for kind in sec.scan_secrets(line[1:]):
                out.append((current, kind))
    return out


def run(host: str = "127.0.0.1", online: bool = False) -> list[tuple[str, str, str]]:
    from .config import load_env

    load_env()
    res: list[tuple[str, str, str]] = []
    add = lambda level, name, detail="": res.append((level, name, detail))

    # ---- access control
    plain, hashed = sec.configured_secret()
    exposed = host not in sec.LOOPBACK
    if hashed:
        add("PASS", "Dashboard password", "stored as a scrypt hash (LEABEE_PASSWORD_HASH)")
    elif plain:
        problem = sec.password_problem(plain)
        add("FAIL" if problem and exposed else "WARN" if problem else "PASS", "Dashboard password", problem or "set and strong enough (consider LEABEE_PASSWORD_HASH)")
    else:
        add("FAIL" if exposed else "WARN", "Dashboard password", f"not set; {'the app is bound to ' + host + ' and anyone can open it' if exposed else 'fine only while bound to localhost'}")
    add("PASS" if not exposed else "WARN", "Network binding", host if not exposed else f"{host} is reachable from other machines: use HTTPS in front (Vercel does) and a password")

    # ---- secrets in git
    ignored = _git("check-ignore", ".env").strip()
    env_exists = (ROOT / ".env").exists()
    add("PASS" if (ignored or not env_exists) else "FAIL", ".env is git-ignored", ".env" if ignored else ("no .env file" if not env_exists else ".env would be committed"))
    tracked = [f for f in _git("ls-files").splitlines() if f]
    leaked = []
    for f in tracked:
        p = ROOT / f
        if p.suffix.lower() in TEXT_EXT and p.is_file() and p.stat().st_size < 1_000_000:
            try:
                for kind in sec.scan_secrets(p.read_text(encoding="utf-8", errors="ignore")):
                    leaked.append(f"{f} ({kind})")
            except OSError:
                pass
    add("FAIL" if leaked else "PASS", "No secrets in tracked files", "; ".join(leaked[:5]) if leaked else f"{len(tracked)} files scanned")
    data_tracked = [f for f in tracked if re.match(r"(data|vercel_data)/|.*\.(db|sqlite|jsonl)$", f) or re.match(r"leads.*\.csv$", f)]
    add("FAIL" if data_tracked else "PASS", "No lead data in git", ", ".join(data_tracked[:4]) if data_tracked else "data/, *.db, exports are not tracked")
    cfg_txt = (ROOT / "config.yaml").read_text(encoding="utf-8", errors="ignore") if (ROOT / "config.yaml").exists() else ""
    add("FAIL" if sec.scan_secrets(cfg_txt) else "PASS", "No keys in config.yaml", "keys belong in .env / secrets")

    # ---- database / transport
    db = os.environ.get("DATABASE_URL", "") or os.environ.get("POSTGRES_URL", "")
    if db:
        remote = not re.search(r"@(localhost|127\.0\.0\.1)", db)
        add("PASS" if (not remote or "sslmode=" in db or "neon.tech" in db) else "WARN", "Database TLS", "remote database; Leabee forces sslmode=require")
        add("PASS" if "-pooler" in db or not remote else "WARN", "Database pooling", "pooled Neon URL" if "-pooler" in db else "use Neon's pooled connection string on Vercel")
    else:
        add("PASS", "Database", "local SQLite file (keep disk encryption on: BitLocker / FileVault)")

    # ---- code hygiene
    bad = []
    for f in tracked:
        p = ROOT / f
        if p.suffix == ".py" and p.is_file() and not f.startswith("tests/") and f != "leabee/audit.py":
            txt = p.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"verify\s*=\s*False|debug\s*=\s*True|shell\s*=\s*True|\beval\(|\bexec\(|pickle\.loads", txt):
                bad.append(f)
    add("FAIL" if bad else "PASS", "No dangerous calls", ", ".join(bad[:4]) if bad else "no verify=False, debug=True, shell=True, eval/exec, pickle")
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8", errors="ignore").splitlines() if (ROOT / "requirements.txt").exists() else []
    unpinned = [r for r in req if r.strip() and not r.startswith("#") and not re.search(r"[<>=~]=?", r)]
    add("WARN" if unpinned else "PASS", "Dependency version floors", ", ".join(unpinned) if unpinned else "all dependencies have minimum versions (CI runs pip-audit; Dependabot opens updates)")
    docker = (ROOT / "Dockerfile").read_text(encoding="utf-8", errors="ignore") if (ROOT / "Dockerfile").exists() else ""
    if docker:
        add("PASS" if re.search(r"^USER\s+(?!root)", docker, re.M) else "WARN", "Docker runs as non-root", "USER set" if "USER " in docker else "add a non-root USER")
    if os.name == "posix" and env_exists:
        mode = (ROOT / ".env").stat().st_mode & 0o077
        add("PASS" if not mode else "WARN", ".env file permissions", "owner-only" if not mode else "chmod 600 .env")

    if online:
        try:
            import requests
            origin = _git("remote", "get-url", "origin").strip()
            m = re.search(r"github\.com[:/]+([^/]+)/([^/.]+)", origin)
            if m:
                r = requests.get(f"https://api.github.com/repos/{m.group(1)}/{m.group(2)}", timeout=15)
                public = r.status_code == 200
                add("WARN" if public else "PASS", "GitHub repository visibility", "PUBLIC: never commit data, keys or exports" if public else "private or not found")
        except Exception:      # noqa: BLE001
            add("WARN", "GitHub repository visibility", "could not check (offline)")
    return res


def report(rows: list[tuple[str, str, str]]) -> tuple[str, int]:
    lines = [f"{lvl:4}  {name}" + (f"  ({detail})" if detail else "") for lvl, name, detail in rows]
    fails = sum(1 for r in rows if r[0] == "FAIL")
    warns = sum(1 for r in rows if r[0] == "WARN")
    lines.append(f"\n{len(rows)} checks: {len(rows) - fails - warns} pass, {warns} warn, {fails} fail")
    return "\n".join(lines), (1 if fails else 0)
