from __future__ import annotations

import csv
import io
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

COLUMNS = ["id", "source", "platform", "lead_type", "title", "text", "url", "author", "author_url",
           "posted_at", "found_at", "categories", "country", "budget_text", "budget_usd", "contact",
           "score", "status", "notes", "enriched", "reasons", "language", "ai_verdict", "ai_summary",
           "follow_up", "deal_value", "notified"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL, platform TEXT NOT NULL, lead_type TEXT NOT NULL,
  title TEXT NOT NULL, text TEXT NOT NULL DEFAULT '', url TEXT NOT NULL,
  author TEXT DEFAULT '', author_url TEXT DEFAULT '',
  posted_at TEXT, found_at TEXT NOT NULL,
  categories TEXT DEFAULT '', country TEXT DEFAULT '',
  budget_text TEXT DEFAULT '', budget_usd REAL,
  contact TEXT DEFAULT '', score INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'new', notes TEXT DEFAULT '',
  enriched INTEGER NOT NULL DEFAULT 0, reasons TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_leads_score ON leads(score DESC);
CREATE INDEX IF NOT EXISTS idx_leads_posted ON leads(posted_at DESC);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT,
  source TEXT, fetched INTEGER, kept INTEGER, new INTEGER, error TEXT
);
CREATE TABLE IF NOT EXISTS saved_filters (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, query TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

# Columns added after the first release; added to existing databases on open.
MIGRATIONS = {
    "language": "TEXT DEFAULT ''",
    "ai_verdict": "TEXT DEFAULT ''",
    "ai_summary": "TEXT DEFAULT ''",
    "follow_up": "TEXT DEFAULT ''",
    "deal_value": "REAL",
    "notified": "INTEGER NOT NULL DEFAULT 0",
}

STATUSES = ("new", "contacted", "replied", "won", "lost", "ignored")
MAX_BUDGET_USD = 2_000_000          # anything above is almost certainly a parsing accident


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str, conn=None):
        """`path` is a SQLite file / ":memory:", or a libsql:// / https:// Turso URL (token from TURSO_AUTH_TOKEN).
        `conn` injects a ready connection (used by tests)."""
        self.path = path
        self.remote = conn is not None or path.startswith(("libsql://", "https://", "wss://"))
        self._lock = threading.RLock()
        if conn is not None:
            self._conn = conn
        elif self.remote:
            from .turso import TursoConnection
            self._conn = TursoConnection(path, os.environ.get("TURSO_AUTH_TOKEN", ""))
        else:
            if path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(path, check_same_thread=False, timeout=60)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        have = {r[1] for r in self._conn.execute("PRAGMA table_info(leads)")}
        for col, ddl in MIGRATIONS.items():
            if col not in have:
                self._conn.execute(f"ALTER TABLE leads ADD COLUMN {col} {ddl}")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_follow ON leads(follow_up)")
        self._conn.commit()

    @contextmanager
    def _tx(self):
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def _many(self, stmts: list[tuple[str, list | tuple]]) -> list[list]:
        """Run several read statements; one HTTP round trip on Turso, plain loop on local SQLite."""
        with self._lock:
            if self.remote:
                return [c.fetchall() for c in self._conn.batch([(q, tuple(a)) for q, a in stmts])]
            return [self._conn.execute(q, a).fetchall() for q, a in stmts]

    # ---------------------------------------------------------------- leads
    def upsert(self, lead: dict) -> bool:
        """Insert or refresh a lead. Returns True if it is new. Keeps user status/notes."""
        lead = dict(lead)
        lead.setdefault("found_at", now_iso())
        bud = lead.get("budget_usd")
        if bud is not None and bud > MAX_BUDGET_USD:
            lead["budget_usd"] = None           # implausible; keep the text only
        with self._tx() as c:
            row = c.execute("SELECT score, enriched FROM leads WHERE id=?", (lead["id"],)).fetchone()
            if row is None:
                if self._is_crosspost(c, lead):
                    return False
                cols = [k for k in COLUMNS if k in lead]
                cur = c.execute(f"INSERT OR IGNORE INTO leads ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                                [lead[k] for k in cols])
                return cur.rowcount > 0         # 0 = another writer inserted it first
            if row["enriched"] and not lead.get("enriched"):
                # never overwrite richer enriched text with a snippet
                return False
            c.execute("""UPDATE leads SET title=?, text=?, categories=?, country=?, budget_text=?, budget_usd=?,
                         contact=?, score=?, reasons=?, language=COALESCE(?, language),
                         enriched=MAX(enriched, ?) WHERE id=?""",
                      (lead["title"], lead["text"], lead["categories"], lead["country"], lead["budget_text"],
                       lead["budget_usd"], lead["contact"], lead["score"], lead.get("reasons", ""),
                       lead.get("language"), int(lead.get("enriched", 0)), lead["id"]))
            return False

    def log_run(self, source: str, started: str, fetched: int, kept: int, new: int, error: str = "") -> None:
        with self._tx() as c:
            c.execute("INSERT INTO runs(started_at, finished_at, source, fetched, kept, new, error) VALUES (?,?,?,?,?,?,?)",
                      (started, now_iso(), source, fetched, kept, new, error))

    def query(self, *, min_score: int = 0, status: str = "", category: str = "", country: str = "",
              platform: str = "", lead_type: str = "", q: str = "", has_contact: bool = False,
              since_hours: float = 0, sort: str = "score", limit: int = 200, offset: int = 0,
              exclude_ignored: bool = True, language: str = "", ai: str = "", follow_due: bool = False,
              ids: list | None = None) -> tuple[list[dict], int]:
        where, args = ["score >= ?"], [min_score]
        if status:
            where.append("status = ?"); args.append(status)
        elif exclude_ignored:
            where.append("status != 'ignored'")
        if category:
            where.append("(',' || categories || ',') LIKE ?"); args.append(f"%,{category},%")
        if country:
            if country == "Unknown":
                where.append("country = ''")
            else:
                where.append("country = ?"); args.append(country)
        if platform:
            where.append("platform = ?"); args.append(platform)
        if lead_type:
            where.append("lead_type = ?"); args.append(lead_type)
        if language:
            if language == "Unknown":
                where.append("language = ''")
            else:
                where.append("language = ?"); args.append(language)
        if ai:
            where.append("ai_verdict = ?"); args.append(ai)
        if has_contact:
            where.append("contact != ''")
        if follow_due:
            where.append("follow_up != '' AND follow_up <= ? AND status IN ('contacted','replied')")
            args.append(datetime.now().strftime("%Y-%m-%d"))
        if ids is not None:
            if not ids:
                return [], 0
            where.append(f"id IN ({','.join('?' * len(ids))})"); args += list(ids)
        if q:
            where.append("(title LIKE ? OR text LIKE ? OR author LIKE ?)")
            args += [f"%{q}%"] * 3
        if since_hours:
            cutoff = datetime.now(timezone.utc).timestamp() - since_hours * 3600
            where.append("CAST(strftime('%s', COALESCE(posted_at, found_at)) AS INTEGER) >= ?")
            args.append(int(cutoff))
        order = {"score": "score DESC, COALESCE(posted_at, found_at) DESC",
                 "new": "COALESCE(posted_at, found_at) DESC",
                 "budget": "budget_usd IS NULL, budget_usd DESC, score DESC",
                 "follow_up": "follow_up = '', follow_up ASC, score DESC"}.get(sort, "score DESC")
        sql_where = " AND ".join(where)
        with self._lock:
            cnt, rows = self._many([(f"SELECT COUNT(*) FROM leads WHERE {sql_where}", args),
                                    (f"SELECT * FROM leads WHERE {sql_where} ORDER BY {order} LIMIT ? OFFSET ?",
                                     args + [limit, offset])])
        return [dict(r) for r in rows], cnt[0][0]

    def get(self, lead_id: str) -> dict | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        return dict(r) if r else None

    def update_user_fields(self, lead_id: str, status: str | None = None, notes: str | None = None,
                           follow_up: str | None = None, deal_value: float | None | str = "unset") -> bool:
        if status is not None and status not in STATUSES:
            raise ValueError(f"invalid status: {status}")
        if follow_up:
            try:
                datetime.strptime(follow_up, "%Y-%m-%d")
            except ValueError:
                raise ValueError("follow_up must be YYYY-MM-DD")
        dv = None
        if deal_value != "unset":
            try:
                dv = None if deal_value in (None, "") else float(deal_value)
            except (TypeError, ValueError):
                raise ValueError("deal_value must be a number")
        with self._tx() as c:
            if status is not None:
                c.execute("UPDATE leads SET status=? WHERE id=?", (status, lead_id))
            if notes is not None:
                c.execute("UPDATE leads SET notes=? WHERE id=?", (notes[:5000], lead_id))
            if follow_up is not None:
                c.execute("UPDATE leads SET follow_up=? WHERE id=?", (follow_up, lead_id))
            if deal_value != "unset":
                c.execute("UPDATE leads SET deal_value=? WHERE id=?", (dv, lead_id))
            return c.execute("SELECT 1 FROM leads WHERE id=?", (lead_id,)).fetchone() is not None

    def bulk_status(self, ids: list[str], status: str) -> int:
        if status not in STATUSES:
            raise ValueError(f"invalid status: {status}")
        if not ids:
            return 0
        with self._tx() as c:
            n = 0
            for i in range(0, len(ids), 500):
                part = ids[i:i + 500]
                n += c.execute(f"UPDATE leads SET status=? WHERE id IN ({','.join('?' * len(part))})",
                               [status, *part]).rowcount
            return n

    # -------------------------------------------------------- AI / alerts
    def for_ai_review(self, limit: int, lo: int = 35, hi: int = 75) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM leads WHERE ai_verdict='' AND status='new' AND score BETWEEN ? AND ? "
                "ORDER BY score DESC LIMIT ?", (lo, hi, limit)).fetchall()
        return [dict(r) for r in rows]

    def set_ai(self, lead_id: str, verdict: str, summary: str, score_delta: int = 0) -> None:
        with self._tx() as c:
            c.execute("UPDATE leads SET ai_verdict=?, ai_summary=?, score=MAX(0, MIN(100, score+?)) WHERE id=?",
                      (verdict, summary[:300], score_delta, lead_id))

    def to_notify(self, min_score: int, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM leads WHERE notified=0 AND status='new' AND score>=? AND ai_verdict!='reject' "
                "ORDER BY score DESC LIMIT ?", (min_score, limit)).fetchall()
        return [dict(r) for r in rows]

    def mark_notified(self, ids: list[str]) -> None:
        with self._tx() as c:
            for i in ids:
                c.execute("UPDATE leads SET notified=1 WHERE id=?", (i,))

    def baseline_notified(self, min_score: int) -> int:
        """Mark everything already stored as notified so the first alert run is not a flood."""
        with self._tx() as c:
            return c.execute("UPDATE leads SET notified=1 WHERE notified=0 AND score>=?", (min_score,)).rowcount

    # ------------------------------------------------------------ meta/filters
    def get_meta(self, key: str, default: str = "") -> str:
        with self._lock:
            r = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r[0] if r else default

    def set_meta(self, key: str, value: str) -> None:
        with self._tx() as c:
            c.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (key, value))

    def list_filters(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute("SELECT id, name, query FROM saved_filters ORDER BY name")]

    def save_filter(self, name: str, query: str) -> None:
        name = name.strip()[:60]
        if not name:
            raise ValueError("name required")
        with self._tx() as c:
            c.execute("INSERT INTO saved_filters(name,query) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET query=excluded.query",
                      (name, query[:1000]))

    def delete_filter(self, fid: int) -> None:
        with self._tx() as c:
            c.execute("DELETE FROM saved_filters WHERE id=?", (fid,))

    # ------------------------------------------------------------------ stats
    def stats(self) -> dict:
        today = datetime.now().strftime("%Y-%m-%d")
        live = "status != 'ignored'"
        (total, hot, with_contact, by_platform, by_country, by_status, by_lang, cat_rows, last, by_type, buckets,
         per_day, budget_rows, perf, revenue, due, ai_n) = self._many([
            (f"SELECT COUNT(*) FROM leads WHERE {live}", ()),
            (f"SELECT COUNT(*) FROM leads WHERE score>=70 AND {live}", ()),
            (f"SELECT COUNT(*) FROM leads WHERE contact!='' AND {live}", ()),
            (f"SELECT platform, COUNT(*) n FROM leads WHERE {live} GROUP BY platform ORDER BY n DESC", ()),
            (f"SELECT CASE WHEN country='' THEN 'Unknown' ELSE country END k, COUNT(*) n FROM leads WHERE {live} GROUP BY k ORDER BY n DESC LIMIT 40", ()),
            ("SELECT status, COUNT(*) n FROM leads GROUP BY status", ()),
            (f"SELECT CASE WHEN language='' THEN 'Unknown' ELSE language END k, COUNT(*) n FROM leads WHERE {live} GROUP BY k ORDER BY n DESC", ()),
            (f"SELECT categories, substr(COALESCE(posted_at, found_at),1,10) d FROM leads WHERE {live}", ()),
            ("SELECT * FROM runs ORDER BY id DESC LIMIT 12", ()),
            (f"SELECT lead_type k, COUNT(*) n FROM leads WHERE {live} GROUP BY k ORDER BY n DESC", ()),
            (f"SELECT CASE WHEN score>=90 THEN '90-100' WHEN score>=80 THEN '80-89' WHEN score>=70 THEN '70-79' "
             f"WHEN score>=60 THEN '60-69' WHEN score>=50 THEN '50-59' ELSE '35-49' END k, COUNT(*) n "
             f"FROM leads WHERE {live} GROUP BY k", ()),
            (f"SELECT substr(COALESCE(posted_at, found_at),1,10) k, COUNT(*) n FROM leads WHERE {live} "
             f"GROUP BY k ORDER BY k DESC LIMIT 30", ()),
            (f"SELECT budget_usd FROM leads WHERE budget_usd IS NOT NULL AND {live}", ()),
            ("SELECT platform, COUNT(*) leads, ROUND(AVG(score),1) avg_score, "
             "SUM(status IN ('contacted','replied','won','lost')) worked, "
             "SUM(status='replied') replied, SUM(status='won') won, "
             "COALESCE(SUM(CASE WHEN status='won' THEN deal_value END),0) revenue "
             "FROM leads WHERE status!='ignored' GROUP BY platform ORDER BY leads DESC", ()),
            ("SELECT COALESCE(SUM(deal_value),0) FROM leads WHERE status='won'", ()),
            ("SELECT COUNT(*) FROM leads WHERE follow_up!='' AND follow_up<=? AND status IN ('contacted','replied')", (today,)),
            (f"SELECT COUNT(*) FROM leads WHERE ai_verdict!='' AND {live}", ()),
        ])
        total, hot, with_contact = total[0][0], hot[0][0], with_contact[0][0]
        revenue, due, ai_n = revenue[0][0], due[0][0], ai_n[0][0]
        budgets = sorted(r[0] for r in budget_rows)
        now_ts = datetime.now(timezone.utc).timestamp()
        mid = datetime.fromtimestamp(now_ts - 7 * 86400, timezone.utc).strftime("%Y-%m-%d")
        old = datetime.fromtimestamp(now_ts - 14 * 86400, timezone.utc).strftime("%Y-%m-%d")
        cats: dict[str, int] = {}
        recent: dict[str, int] = {}
        prev: dict[str, int] = {}
        for r in cat_rows:
            day = r[1] or ""
            for k in filter(None, (r[0] or "").split(",")):
                cats[k] = cats.get(k, 0) + 1
                if day >= mid:
                    recent[k] = recent.get(k, 0) + 1
                elif day >= old:
                    prev[k] = prev.get(k, 0) + 1
        trend = sorted(({"k": k, "recent": recent.get(k, 0), "prev": prev.get(k, 0)} for k in cats),
                       key=lambda x: -(x["recent"] - x["prev"]))
        return {
            "total": total, "hot": hot, "with_contact": with_contact,
            "by_platform": [dict(r) for r in by_platform],
            "by_country": [dict(r) for r in by_country],
            "by_status": [dict(r) for r in by_status],
            "by_language": [dict(r) for r in by_lang],
            "by_category": sorted(({"k": k, "n": n} for k, n in cats.items()), key=lambda x: -x["n"]),
            "runs": [dict(r) for r in last],
            "by_type": [dict(r) for r in by_type],
            "by_score": sorted((dict(r) for r in buckets), key=lambda x: x["k"], reverse=True),
            "per_day": [dict(r) for r in reversed(per_day)],
            "with_budget": len(budgets), "budget_median": budgets[len(budgets) // 2] if budgets else 0,
            "platform_perf": [dict(r) for r in perf],
            "category_trend": trend,
            "revenue": revenue, "follow_ups_due": due, "ai_reviewed": ai_n,
        }

    def unenriched(self, limit: int, min_score: int = 0) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM leads WHERE enriched=0 AND status='new' AND score>=? "
                "AND source='tinyfish' ORDER BY score DESC LIMIT ?", (min_score, limit)).fetchall()
        return [dict(r) for r in rows]

    def _is_crosspost(self, c, lead: dict) -> bool:
        author, title = (lead.get("author") or "").strip(), (lead.get("title") or "").strip().lower()
        if not author or len(title) < 15:
            return False
        return c.execute("SELECT 1 FROM leads WHERE author=? AND lower(title)=? AND id!=? LIMIT 1",
                         (author, title, lead["id"])).fetchone() is not None

    def dedupe(self) -> int:
        """Remove later cross-posts (same author + same title) that you have not worked yet."""
        with self._tx() as c:
            cur = c.execute("""DELETE FROM leads WHERE status='new' AND author!='' AND length(title)>=15 AND rowid NOT IN (
                                 SELECT MIN(rowid) FROM leads WHERE author!='' AND length(title)>=15
                                 GROUP BY author, lower(title))
                               AND (author, lower(title)) IN (
                                 SELECT author, lower(title) FROM leads GROUP BY author, lower(title) HAVING COUNT(*)>1)""")
            return cur.rowcount

    def delete(self, lead_id: str) -> None:
        with self._tx() as c:
            c.execute("DELETE FROM leads WHERE id=?", (lead_id,))

    def _copy_to(self, dst: sqlite3.Connection) -> None:
        """Copy the whole database into an open local SQLite connection (works for local files and Turso)."""
        with self._lock:
            if not self.remote:
                self._conn.backup(dst)
                return
            dst.executescript(SCHEMA)
            have = {r[1] for r in dst.execute("PRAGMA table_info(leads)")}
            for col, ddl in MIGRATIONS.items():
                if col not in have:
                    dst.execute(f"ALTER TABLE leads ADD COLUMN {col} {ddl}")
            for table in ("leads", "runs", "saved_filters", "meta"):
                rows = self._conn.execute(f"SELECT * FROM {table}").fetchall()
                if not rows:
                    continue
                cols = rows[0].keys()
                dst.executemany(f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                                [tuple(r) for r in rows])
            dst.commit()

    def backup(self, dest_dir: str, keep: int = 14) -> str | None:
        """Consistent local copy of the database. Prunes old copies."""
        if self.path == ":memory:":
            return None
        d = Path(dest_dir)
        d.mkdir(parents=True, exist_ok=True)
        out = d / f"leads-{datetime.now():%Y%m%d-%H%M%S}.db"
        dst = sqlite3.connect(str(out))
        try:
            self._copy_to(dst)
        finally:
            dst.close()
        for old in sorted(d.glob("leads-*.db"))[:-keep]:
            old.unlink(missing_ok=True)
        return str(out)

    def to_csv(self, rows: list[dict]) -> str:
        buf = io.StringIO()
        cols = ["score", "status", "platform", "lead_type", "title", "url", "author", "author_url", "country",
                "language", "categories", "budget_text", "budget_usd", "contact", "ai_summary", "follow_up",
                "deal_value", "posted_at", "found_at", "notes", "text"]
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            r = dict(r)
            r["text"] = (r.get("text") or "")[:1500].replace("\r", " ")
            w.writerow(r)
        return buf.getvalue()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def open_store(cfg: dict, local: bool = False) -> Store:
    """Turso when TURSO_DATABASE_URL is set (shared, persistent), otherwise the local SQLite file."""
    url = os.environ.get("TURSO_DATABASE_URL", "").strip()
    if url and not local:
        return Store(url)
    return Store(cfg["database"])


def migrate(src: Store, dst: Store, batch_size: int = 40) -> dict:
    """Copy every table from one store to another (e.g. local SQLite -> Turso). Safe to re-run."""
    counts = {}
    for table in ("leads", "saved_filters", "meta", "runs"):
        rows = src._many([(f"SELECT * FROM {table}", ())])[0]
        counts[table] = len(rows)
        if not rows:
            continue
        cols = rows[0].keys()
        if table == "runs":
            cols = [c for c in cols if c != "id"]
        sql = f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
        for i in range(0, len(rows), batch_size):
            chunk = [(sql, tuple(r[c] for c in cols)) for r in rows[i:i + batch_size]]
            if dst.remote:
                dst._conn.batch(chunk)
            else:
                with dst._tx() as c:
                    for q, a in chunk:
                        c.execute(q, a)
    return counts
