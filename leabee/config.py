"""Config + .env loading (no external dotenv dependency)."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path | None = None) -> None:
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v


def load_config(path: str | Path | None = None) -> dict:
    load_env()
    path = Path(path) if path else ROOT / "config.yaml"
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    cfg.setdefault("database", "data/leads.db")
    cfg.setdefault("min_score", 35)
    cfg.setdefault("max_age_days", 21)
    cfg.setdefault("hot_score", 70)
    cfg.setdefault("request_delay", 1.5)
    if os.environ.get("LEABEE_DB"):
        cfg["database"] = os.environ["LEABEE_DB"]
    db = Path(cfg["database"])
    if not db.is_absolute():
        db = ROOT / db
    cfg["database"] = str(db)
    return cfg
