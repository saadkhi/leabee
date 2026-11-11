from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from .config import load_config
from .pipeline import ALL_SOURCES, enrich, run_all
from .storage import Store, migrate, open_store


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _print_leads(rows, limit=25):
    for r in rows[:limit]:
        flag = "HOT " if r["score"] >= 70 else "    "
        print(f"{flag}{r['score']:>3}  {r['platform'][:14]:<14} {(r['country'] or '-')[:14]:<14} "
              f"{r['title'][:70]}\n          {r['url']}")
        if r["contact"]:
            print(f"          contact: {r['contact'][:100]}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="leabee", description="Worldwide remote-work lead finder")
    p.add_argument("-c", "--config", help="path to config.yaml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="gather and score new leads")
    c.add_argument("-s", "--sources", default=",".join(ALL_SOURCES),
                   help=f"comma list of: {', '.join(ALL_SOURCES)}")
    c.add_argument("--partial", action="store_true", help="rotating slice of Reddit (short run) instead of the full sweep")
    c.add_argument("--enrich", type=int, default=0, metavar="N", help="after collecting, fetch full text for top N TinyFish leads")

    e = sub.add_parser("enrich", help="fetch full page text for top TinyFish leads and re-score")
    e.add_argument("-n", "--limit", type=int, default=100)

    l = sub.add_parser("list", help="show top leads")
    l.add_argument("-n", "--limit", type=int, default=25)
    l.add_argument("--min-score", type=int, default=0)
    l.add_argument("--category", default="")
    l.add_argument("--country", default="")
    l.add_argument("--contact", action="store_true", help="only leads with contact details")

    x = sub.add_parser("export", help="export leads to CSV")
    x.add_argument("-o", "--out", default="leads.csv")
    x.add_argument("--min-score", type=int, default=0)

    sub.add_parser("stats", help="show counts")
    sub.add_parser("rescore", help="re-run classification on stored leads (after tuning rules)")

    s = sub.add_parser("serve", help="run the web dashboard")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--auto-collect", type=float, default=None, metavar="MIN",
                   help="also collect in the background every MIN minutes (0 = off; default from config schedule)")

    ar = sub.add_parser("ai-review", help="AI second opinion on borderline leads (needs ANTHROPIC_API_KEY)")
    ar.add_argument("-n", "--limit", type=int, default=None)
    sub.add_parser("alert", help="send alerts for new hot leads (Telegram / Discord / email, see .env.example)")
    sub.add_parser("backup", help="copy the database to data/backups")
    ev = sub.add_parser("eval", help="measure scoring accuracy against hand-labelled leads in data/eval/*.json")
    ev.add_argument("files", nargs="*")
    ev.add_argument("-t", "--threshold", type=int, default=60)
    ev.add_argument("--no-errors", action="store_true")
    dr = sub.add_parser("dump-raw", help="save raw collected items (before any filtering) to a JSONL file, for labelling and tuning")
    dr.add_argument("-s", "--sources", default="hackernews,boards,mastodon,github,reddit")
    dr.add_argument("-o", "--out", default="data/eval/raw.jsonl")
    dr.add_argument("--partial", action="store_true")
    es2 = sub.add_parser("eval-sample", help="draw a fresh blind sample from raw dumps for hand labelling (posts only, no verdicts)")
    es2.add_argument("raw", nargs="+")
    es2.add_argument("-x", "--exclude", nargs="*", default=[])
    es2.add_argument("-o", "--out", default="data/eval/labeled_new.json")
    sub.add_parser("copilot", help="run the Parlant sales copilot server (needs parlant[anthropic] and ANTHROPIC_API_KEY)")
    sub.add_parser("bot", help="run the Telegram bot (buttons on alerts, /top /stats /due)")
    sub.add_parser("digest", help="send the daily digest to your alert channels")
    sub.add_parser("tools", help="show which extractor tools (Crawl4AI, AgentQL, Browser Use, Skyvern, Stagehand...) are ready")
    im = sub.add_parser("import-maps", help="import a Google Maps Extractor export (.csv/.json) as prospects")
    im.add_argument("file")
    es = sub.add_parser("enrich-sites", help="crawl company websites of top leads for emails, phones and socials")
    es.add_argument("-n", "--limit", type=int, default=25)
    es.add_argument("--min-score", type=int, default=50)
    es.add_argument("--agents", action="store_true", help="allow AI browser agents as a last resort (needs keys)")
    sub.add_parser("migrate-to-remote", help="copy the local SQLite database into Neon (needs DATABASE_URL)")
    sub.add_parser("publish", help="snapshot, commit and push to GitHub so Vercel redeploys (private repos only)")
    sn = sub.add_parser("snapshot", help="write vercel_data/leads.sqlite (read-only copy for the Vercel deployment)")
    sn.add_argument("--min-score", type=int, default=0)
    d = sub.add_parser("draft", help="print reply drafts for a lead id")
    d.add_argument("lead_id")

    w = sub.add_parser("watch", help="collect repeatedly on an interval")
    w.add_argument("--every", type=float, default=60, help="minutes between runs")
    w.add_argument("-s", "--sources", default=",".join(ALL_SOURCES))
    w.add_argument("--full", action="store_true", help="full Reddit sweep every run (slow) instead of a rotating slice")

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    a = p.parse_args(argv)
    _setup_logging(a.verbose)
    cfg = load_config(a.config)
    store = open_store(cfg)

    if a.cmd == "collect":
        srcs = [s.strip() for s in a.sources.split(",") if s.strip()]
        bad = [s for s in srcs if s not in ALL_SOURCES]
        if bad:
            print(f"unknown source(s): {bad}. choose from {ALL_SOURCES}", file=sys.stderr)
            return 2
        res = run_all(cfg, store, srcs, progress=print, partial=a.partial)
        if a.enrich:
            print("enrich:", enrich(cfg, store, a.enrich, progress=print))
        from . import ai, notify
        ai_res = ai.review(cfg, store, progress=print)
        if ai_res.get("reviewed"):
            print("ai:", ai_res)
        al = notify.alert_new_hot(cfg, store)
        if al.get("sent"):
            print("alerts:", al)
        from .jobs import publish_snapshot
        pub = publish_snapshot(cfg, store)
        if pub.get("published") or pub.get("error"):
            print("publish:", pub)
        print(f"\nTOTAL new leads: {sum(r['new'] for r in res)}  | in database: {store.stats()['total']}")
        return 0 if not any(r["error"] for r in res) else 1
    if a.cmd == "enrich":
        print(enrich(cfg, store, a.limit, progress=print))
        return 0
    if a.cmd == "list":
        rows, total = store.query(min_score=a.min_score, category=a.category, country=a.country,
                                  has_contact=a.contact, limit=a.limit)
        _print_leads(rows, a.limit)
        print(f"\n{total} matching leads")
        return 0
    if a.cmd == "export":
        rows, total = store.query(min_score=a.min_score, limit=10 ** 6)
        Path(a.out).write_text(store.to_csv(rows), encoding="utf-8-sig", newline="")
        print(f"wrote {total} leads to {a.out}")
        return 0
    if a.cmd == "rescore":
        from .pipeline import rescore
        print(rescore(cfg, store))
        return 0
    if a.cmd == "stats":
        st = store.stats()
        print(f"total={st['total']} hot={st['hot']} with_contact={st['with_contact']}")
        for k in ("by_platform", "by_category", "by_country"):
            print(k + ":", ", ".join(f"{d.get('platform') or d.get('k')}={d['n']}" for d in st[k][:15]))
        return 0
    if a.cmd == "ai-review":
        from . import ai
        print(ai.review(cfg, store, a.limit, progress=print))
        return 0
    if a.cmd == "alert":
        from . import notify
        print(notify.alert_new_hot(cfg, store))
        return 0
    if a.cmd == "backup":
        from .jobs import backup_dir
        print("backup:", store.backup(backup_dir(cfg), int((cfg.get("backup", {}) or {}).get("keep", 14))))
        return 0
    if a.cmd == "eval-sample":
        from .evaluate import sample_blind
        for line in sample_blind(cfg, a.raw, a.exclude, a.out):
            print(line)
        return 0
    if a.cmd == "copilot":
        from .copilot.agent import run as run_copilot
        run_copilot()
        return 0
    if a.cmd == "bot":
        from .telegram_bot import run as run_bot
        run_bot(cfg, store)
        return 0
    if a.cmd == "digest":
        from .digest import build, send_digest
        res = send_digest(cfg, store)
        print(res["text"] if not res.get("sent") else f"sent via {res['channels']}")
        return 0
    if a.cmd == "tools":
        from .extractors import status
        for t in status():
            print(f"{'ready ' if t['ready'] else 'off   '} {t['kind']:5} {t['name']:12} {t['how_to_enable']}")
        return 0
    if a.cmd == "import-maps":
        from .pipeline import item_to_lead
        from .prospects import read_export, record_to_item
        n = kept = new = 0
        for rec in read_export(a.file):
            it = record_to_item(rec)
            n += 1
            lead = item_to_lead(it, cfg) if it else None
            if lead:
                kept += 1
                new += store.upsert(lead)
        print(f"read {n} businesses, kept {kept} prospects, {new} new")
        return 0
    if a.cmd == "enrich-sites":
        from .pipeline import enrich_sites
        print(enrich_sites(cfg, store, a.limit, a.min_score, a.agents, progress=print))
        return 0
    if a.cmd == "dump-raw":
        import dataclasses
        import json as _json
        from .collectors import registry
        from .http import Http
        run_cfg = {**cfg, "_partial": a.partial}
        http, n = Http(delay=float(cfg.get("request_delay", 1.5))), 0
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            for name in [x.strip() for x in a.sources.split(",") if x.strip()]:
                for it in registry()[name](run_cfg, http):
                    d = dataclasses.asdict(it)
                    d["posted_at"] = it.posted_at.isoformat() if it.posted_at else None
                    f.write(_json.dumps(d, ensure_ascii=False) + "\n")
                    n += 1
                print(f"{name}: running total {n}", flush=True)
        print(f"wrote {n} raw items to {a.out}")
        return 0
    if a.cmd == "eval":
        from .evaluate import run as run_eval
        print(run_eval(cfg, a.files or None, a.threshold, not a.no_errors))
        return 0
    if a.cmd == "migrate-to-remote":
        if not store.remote:
            print("Set DATABASE_URL (your Neon connection string) in .env first.", file=sys.stderr)
            return 2
        local = Store(cfg["database"])
        print("copying", cfg["database"], "->", store.where)
        res = migrate(local, store)
        print("done:", res, "| leads now in Neon:", store.stats()["total"])
        return 0
    if a.cmd == "publish":
        from .jobs import publish_snapshot
        res = publish_snapshot(cfg, store, force=True)
        print(res)
        return 0 if res.get("published") or res.get("skipped") else 1
    if a.cmd == "snapshot":
        from .config import ROOT
        from .jobs import make_snapshot
        out = ROOT / "vercel_data" / "leads.sqlite"
        n = make_snapshot(store, out, a.min_score)
        print(f"snapshot: {n} leads -> {out}  ({out.stat().st_size / 1e6:.1f} MB)")
        return 0
    if a.cmd == "draft":
        from .outreach import draft
        lead = store.get(a.lead_id)
        if not lead:
            print("lead not found", file=sys.stderr)
            return 1
        for k, v in draft(lead).items():
            print(f"--- {k} ---\n{v}\n")
        return 0
    if a.cmd == "serve":
        from .jobs import Scheduler, maybe_backup
        from .web import create_app
        app = create_app(cfg, store)
        sched = cfg.get("schedule", {}) or {}
        every = a.auto_collect if a.auto_collect is not None else (float(sched.get("every_minutes", 0)) if sched.get("enabled") else 0)
        try:
            maybe_backup(cfg, store)
        except Exception as e:
            print(f"backup skipped: {e}")
        if every:
            Scheduler(cfg, store, every, partial=bool(sched.get("partial", True)), log_sink=app.config["state_say"]).start()
            print(f"Auto-collect every {every:g} min")
        print(f"Dashboard: http://{a.host}:{a.port}")
        app.run(host=a.host, port=a.port, threaded=True, debug=False)
        return 0
    if a.cmd == "watch":
        srcs = [s.strip() for s in a.sources.split(",") if s.strip()]
        from .jobs import maybe_backup, run_cycle
        while True:
            run_cycle(cfg, store, srcs, partial=not a.full, progress=print)
            maybe_backup(cfg, store)
            print(f"sleeping {a.every} min ...")
            time.sleep(a.every * 60)
    return 0
