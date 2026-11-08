from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from .config import load_config
from .pipeline import ALL_SOURCES, enrich, run_all
from .storage import Store


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

    w = sub.add_parser("watch", help="collect repeatedly on an interval")
    w.add_argument("--every", type=float, default=60, help="minutes between runs")
    w.add_argument("-s", "--sources", default=",".join(ALL_SOURCES))

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    a = p.parse_args(argv)
    _setup_logging(a.verbose)
    cfg = load_config(a.config)
    store = Store(cfg["database"])

    if a.cmd == "collect":
        srcs = [s.strip() for s in a.sources.split(",") if s.strip()]
        bad = [s for s in srcs if s not in ALL_SOURCES]
        if bad:
            print(f"unknown source(s): {bad}. choose from {ALL_SOURCES}", file=sys.stderr)
            return 2
        res = run_all(cfg, store, srcs, progress=print)
        if a.enrich:
            print("enrich:", enrich(cfg, store, a.enrich, progress=print))
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
    if a.cmd == "serve":
        from .web import create_app
        app = create_app(cfg, store)
        print(f"Dashboard: http://{a.host}:{a.port}")
        app.run(host=a.host, port=a.port, threaded=True, debug=False)
        return 0
    if a.cmd == "watch":
        srcs = [s.strip() for s in a.sources.split(",") if s.strip()]
        while True:
            run_all(cfg, store, srcs, progress=print)
            print(f"sleeping {a.every} min ...")
            time.sleep(a.every * 60)
    return 0
