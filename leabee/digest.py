"""Daily digest: the best new leads, follow-ups due, and what is working. Sent to every configured alert channel."""
from __future__ import annotations

from datetime import datetime

from . import notify
from .storage import Store


def build(store: Store, cfg: dict, top: int = 10) -> dict:
    st = store.stats()
    new, _ = store.query(status="new", min_score=50, since_hours=48, limit=top)
    due, _ = store.query(follow_due=True, limit=10, sort="follow_up")
    best = max(st["platform_perf"], key=lambda p: (p["won"], p["avg_score"]), default=None)
    lines = [f"Leabee digest, {datetime.now():%A %d %B}", "",
             f"{st['total']} active leads | {st['hot']} hot | {st['with_contact']} with contact | {st['follow_ups_due']} follow-ups due", ""]
    lines.append(f"Top {len(new)} new leads (last 48h, grade C+):")
    for l in new:
        why = (l.get("next_action") or "")[:80]
        lines.append(f"  {l.get('grade') or '-'} {l['score']:>3}  {l['platform'][:12]:12} {l['title'][:70]}\n        {why}\n        {l['url']}")
    if not new:
        lines.append("  none")
    lines += ["", "Follow-ups due:"] + ([f"  {l['follow_up']}  {l['title'][:70]}" for l in due] or ["  none"])
    if best and best["leads"]:
        lines += ["", f"Best source so far: {best['platform']} (avg score {best['avg_score']}, {best['won']} won)"]
    return {"subject": f"Leabee: {len(new)} new leads, {len(due)} follow-ups due", "text": "\n".join(lines), "new": len(new), "due": len(due)}


def send_digest(cfg: dict, store: Store) -> dict:
    d = build(store, cfg)
    if not notify.channels():
        return {**d, "sent": False, "skipped": "no alert channel configured"}
    ok = notify.send(d["text"], d["subject"])
    return {**d, "sent": bool(ok), "channels": ok}
