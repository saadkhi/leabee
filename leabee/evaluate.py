"""Measure scoring accuracy against hand-labelled leads.

Labels: B = genuine buyer of a remote-doable service (freelance / contract / project / paid task)
        J = full-time employee job ad        N = not a lead (seller, job seeker, advice, discussion, closed, off-topic)

Label files are JSON {"eval_now": ISO, "rows": [{label, source, platform, lead_type, title, text, url, author, posted_at, ...}]}.
They contain real people's public posts, so they live in data/eval/ (git-ignored) and are never committed.
`python -m leabee eval` scores every data/eval/*.json file with the current rules.
"""
from __future__ import annotations

import json
from pathlib import Path

from .models import RawItem
from .pipeline import item_to_lead
from .textutil import parse_dt

THRESHOLDS = (35, 50, 60, 70, 80)


def load(path: str | Path) -> tuple[str, list[dict]]:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    return d.get("eval_now", ""), d["rows"]


def predict(rows: list[dict], cfg: dict, eval_now: str = "") -> list[dict]:
    """Score every labelled row with the current rules. Dropped rows get score 0."""
    now = parse_dt(eval_now) if eval_now else None
    cfg = {**cfg, "max_age_days": 10 ** 6, "min_score": 0}
    out = []
    for r in rows:
        item = RawItem(source=r["source"], platform=r["platform"], lead_type=r["lead_type"], title=r["title"],
                       text=r["text"], url=r["url"], author=r.get("author", ""), author_url=r.get("author_url", ""),
                       posted_at=parse_dt(r.get("posted_at")), location_hint=r.get("country", "") or "")
        lead = item_to_lead(item, cfg, now)
        out.append({**r, "score": lead["score"] if lead else 0, "kept": lead is not None,
                    "grade": (lead or {}).get("grade", ""), "kind": (lead or {}).get("kind", ""),
                    "reasons": (lead or {}).get("reasons", "")})
    return out


def metrics(pred: list[dict]) -> dict:
    pos = [p for p in pred if p["label"] == "B"]
    neg = [p for p in pred if p["label"] != "B"]
    table = {}
    for t in THRESHOLDS:
        sel = [p for p in pred if p["kept"] and p["score"] >= t]
        tp = sum(1 for p in sel if p["label"] == "B")
        precision = tp / len(sel) if sel else 0.0
        recall = tp / len(pos) if pos else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        table[t] = {"returned": len(sel), "precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
                    "job_ads": sum(1 for p in sel if p["label"] == "J"), "not_leads": sum(1 for p in sel if p["label"] == "N")}
    # ranking quality: how often a real buyer outranks a non-buyer (ties count half)
    wins = ties = total = 0
    for a in pos:
        for b in neg:
            total += 1
            if a["score"] > b["score"]:
                wins += 1
            elif a["score"] == b["score"]:
                ties += 1
    auc = (wins + ties / 2) / total if total else 0.0
    return {"n": len(pred), "buyers": len(pos), "table": table, "auc": round(auc, 3),
            "noise_dropped": round(sum(1 for p in neg if not p["kept"]) / len(neg), 3) if neg else 0.0}


def report(name: str, m: dict) -> str:
    lines = [f"{name}: {m['n']} leads, {m['buyers']} real buyers | ranking AUC {m['auc']:.3f} | "
             f"{m['noise_dropped']:.0%} of non-leads dropped outright",
             "  score>= | returned | precision | recall |  F1  | job ads | not leads"]
    for t, r in m["table"].items():
        lines.append(f"  {t:>7} | {r['returned']:>8} | {r['precision']:>9.0%} | {r['recall']:>6.0%} | {r['f1']:.2f} | "
                     f"{r['job_ads']:>7} | {r['not_leads']:>9}")
    return "\n".join(lines)


def errors(pred: list[dict], threshold: int = 60, limit: int = 40) -> list[str]:
    """Human-readable list of the mistakes at a threshold (for tuning)."""
    out = []
    for p in pred:
        hit = p["kept"] and p["score"] >= threshold
        if hit and p["label"] != "B":
            out.append(f"FALSE+ [{p['label']}] {p['score']:>3} {p['platform'][:9]:9} {p['title'][:90]}  ({p['reasons']})")
        elif not hit and p["label"] == "B":
            out.append(f"MISSED [B] {p['score']:>3} {p['platform'][:9]:9} {p['title'][:90]}  ({p['reasons']})")
    return out[:limit]


def run(cfg: dict, paths: list[str] | None = None, threshold: int = 60, show_errors: bool = True) -> str:
    files = [Path(p) for p in paths] if paths else sorted((Path(cfg["database"]).parent / "eval").glob("*.json"))
    if not files:
        return "No labelled files found in data/eval/*.json"
    out = []
    for f in files:
        eval_now, rows = load(f)
        pred = predict(rows, cfg, eval_now)
        out.append(report(f.stem, metrics(pred)))
        if show_errors:
            out += ["  mistakes at score>=%d:" % threshold] + ["    " + e for e in errors(pred, threshold)]
    return "\n".join(out)


def sample_blind(cfg: dict, raw_paths: list[str], exclude_paths: list[str], out_path: str, keep: int = 30, near: int = 20, rest: int = 10,
                 seed: int = 31) -> list[str]:
    """Draw a fresh sample from raw (unfiltered) items for hand labelling. Items in exclude_paths are skipped. Writes label "?" rows to
    out_path and returns printable lines that show ONLY the posts (never the scorer's verdict), so labels stay unbiased."""
    import random
    import re

    from .classify import detect_categories

    seen = set()
    for f in exclude_paths:
        seen |= {r["url"] for r in json.loads(Path(f).read_text(encoding="utf-8"))["rows"]}
    rows, urls = [], set()
    for path in raw_paths:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d["url"] in seen or d["url"] in urls or not d["title"]:
                continue
            urls.add(d["url"])
            rows.append({"label": "?", "source": d["source"], "platform": d["platform"], "lead_type": d["lead_type"], "title": d["title"], "text": d["text"],
                         "url": d["url"], "author": d.get("author", ""), "author_url": d.get("author_url", ""), "posted_at": d["posted_at"],
                         "country": d.get("location_hint", "")})
    now = "2026-11-10T12:00:00+00:00"
    pred = predict(rows, cfg, now)
    kept = [p for p in pred if p["kept"] and p["score"] >= 35]
    nearmiss = [p for p in pred if not p["kept"] and detect_categories(p["title"] + " " + p["text"][:500]) and
                re.search(r"hir|looking for|need|wanted|seeking|budget|paid|freelanc|contract", p["title"] + " " + p["text"][:300], re.I)]
    other = [p for p in pred if not p["kept"] and p not in nearmiss]
    rnd = random.Random(seed)
    for g in (kept, nearmiss, other):
        rnd.shuffle(g)
    pick = kept[:keep] + nearmiss[:near] + other[:rest]
    rnd.shuffle(pick)
    keys = ("label", "source", "platform", "lead_type", "title", "text", "url", "author", "author_url", "posted_at", "country")
    out = [{k: p[k] for k in keys} for p in pick]
    Path(out_path).write_text(json.dumps({"eval_now": now, "rows": out}, ensure_ascii=False, indent=1), encoding="utf-8")
    return [f"{i:3}|{o['platform'][:8]:8}|{o['title'][:78]}|| {' '.join(o['text'].split())[:170]}" for i, o in enumerate(out)]
