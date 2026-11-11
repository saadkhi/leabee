from __future__ import annotations

import csv
import io
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .security import csv_safe

COLUMNS = ["id", "source", "platform", "lead_type", "title", "text", "url", "author", "author_url",
           "posted_at", "found_at", "categories", "country", "budget_text", "budget_usd", "contact",
           "score", "status", "notes", "enriched", "reasons", "language", "ai_verdict", "ai_summary",
           "follow_up", "deal_value", "notified", "kind", "grade", "next_action", "components", "details"]

# Written in the common subset of SQLite and Postgres; _pg_ddl() adapts the two type differences.
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
    "kind": "TEXT DEFAULT ''",
    "grade": "TEXT DEFAULT ''",
    "next_action": "TEXT DEFAULT ''",
    "components": "TEXT DEFAULT ''",
    "details": "TEXT DEFAULT ''",
}
_TABLES = ("leads", "runs", "saved_filters", "meta")

STATUSES = ("new", "contacted", "replied", "won", "lost", "ignored")
MAX_BUDGET_USD = 2_000_000          # anything above is almost certainly a parsing accident
_SCHEMA_LOCK_ID = 727274            # Postgres advisory lock so two cold starts don't race on CREATE TABLE


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _pg_ddl(text: str) -> str:
    text = text.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
    return re.sub(r"\bREAL\b", "DOUBLE PRECISION", text)


def database_url() -> str:
    """Neon/Postgres connection string from the environment, or ''. DATABASE_URL first, then POSTGRES_URL."""
    for name in ("DATABASE_URL", "POSTGRES_URL"):
        v = os.environ.get(name, "").strip()
        if v.startswith(("postgres://", "postgresql://")):
            return v
    return ""


class Store:
    def __init__(self, path: str, conn=None):
        """`path` is a SQLite file, ":memory:" or a postgres:// URL (Neon). `conn` injects a ready connection (tests)."""
        self.path = path
        self.pg = conn is not None or path.startswith(("postgres://", "postgresql://"))
        self.remote = self.pg                   # hosted database: no local file to back up or snapshot directly
        self._lock = threading.RLock()
        if conn is not None:
            self._conn = conn
        elif self.pg:
            from .dbconn import PgConnection
            self._conn = PgConnection(path)
        else:
            if path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(path, check_same_thread=False, timeout=60)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    @property
    def where(self) -> str:
        """Human-readable location without credentials."""
        if self.pg:
            u = urlsplit(self.path)
            return f"postgres://{u.hostname or 'database'}{u.path}"
        return self.path

    def _init_schema(self) -> None:
        if not self.pg:
            self._conn.executescript(SCHEMA)
            have = {r[1] for r in self._conn.execute("PRAGMA table_info(leads)")}
            for col, ddl in MIGRATIONS.items():
                if col not in have:
                    self._conn.execute(f"ALTER TABLE leads ADD COLUMN {col} {ddl}")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_follow ON leads(follow_up)")
            self._conn.commit()
            return
        rows = self._conn.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name IN ('leads','runs','saved_filters','meta')").fetchall()
        tables: dict[str, set] = {}
        for r in rows:
            tables.setdefault(r[0], set()).add(r[1])
        if set(tables) == set(_TABLES) and set(COLUMNS) <= tables["leads"]:
            return                              # already set up: cold starts skip all DDL
        stmts = [(s.strip(), ()) for s in _pg_ddl(SCHEMA).split(";") if s.strip()]
        stmts += [(f"ALTER TABLE leads ADD COLUMN IF NOT EXISTS {c} {_pg_ddl(d)}", ()) for c, d in MIGRATIONS.items()]
        stmts.append(("CREATE INDEX IF NOT EXISTS idx_leads_follow ON leads(follow_up)", ()))
        self._conn.batch([("SELECT pg_advisory_xact_lock(%d)" % _SCHEMA_LOCK_ID, ())] + stmts, atomic=True)

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
        """Run several read statements and return their rows."""
        with self._lock:
            if self.pg:
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
                head, tail = ("INSERT INTO", " ON CONFLICT DO NOTHING") if self.pg else ("INSERT OR IGNORE INTO", "")
                cur = c.execute(f"{head} leads ({','.join(cols)}) VALUES ({','.join('?' * len(cols))}){tail}",
                                [lead[k] for k in cols])
                return cur.rowcount > 0         # 0 = another writer inserted it first
            if row["enriched"] and not lead.get("enriched"):
                # never overwrite richer enriched text with a snippet
                return False
            en = int(lead.get("enriched", 0))
            c.execute("""UPDATE leads SET title=?, text=?, categories=?, country=?, budget_text=?, budget_usd=?,
                         contact=?, score=?, reasons=?, language=COALESCE(?, language),
                         kind=COALESCE(?, kind), grade=COALESCE(?, grade), next_action=COALESCE(?, next_action),
                         components=COALESCE(?, components), details=COALESCE(?, details),
                         enriched=CASE WHEN enriched > ? THEN enriched ELSE ? END WHERE id=?""",
                      (lead["title"], lead["text"], lead["categories"], lead["country"], lead["budget_text"],
                       lead["budget_usd"], lead["contact"], lead["score"], lead.get("reasons", ""),
                       lead.get("language"), lead.get("kind"), lead.get("grade"), lead.get("next_action"),
                       lead.get("components"), lead.get("details"), en, en, lead["id"]))
            return False

    def log_run(self, source: str, started: str, fetched: int, kept: int, new: int, error: str = "") -> None:
        with self._tx() as c:
            c.execute("INSERT INTO runs(started_at, finished_at, source, fetched, kept, new, error) VALUES (?,?,?,?,?,?,?)",
                      (started, now_iso(), source, fetched, kept, new, error))

    def query(self, *, min_score: int = 0, status: str = "", category: str = "", country: str = "",
              platform: str = "", lead_type: str = "", q: str = "", has_contact: bool = False,
              since_hours: float = 0, sort: str = "score", limit: int = 200, offset: int = 0,
              exclude_ignored: bool = True, language: str = "", ai: str = "", follow_due: bool = False,
              ids: list | None = None, grade: str = "", kind: str = "") -> tuple[list[dict], int]:
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
        if grade:
            where.append("grade = ?"); args.append(grade)
        if kind:
            where.append("kind = ?"); args.append(kind)
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
            like = "ILIKE" if self.pg else "LIKE"      # SQLite LIKE is already case-insensitive
            where.append(f"(title {like} ? OR text {like} ? OR author {like} ?)")
            args += [f"%{q}%"] * 3
        if since_hours:
            # all timestamps are stored as UTC ISO-8601 text, which sorts chronologically
            cutoff = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() - since_hours * 3600, timezone.utc)
            where.append("COALESCE(posted_at, found_at) >= ?")
            args.append(cutoff.isoformat(timespec="seconds"))
        order = {"score": "score DESC, COALESCE(posted_at, found_at) DESC",
                 "new": "COALESCE(posted_at, found_at) DESC",
                 "budget": "budget_usd IS NULL, budget_usd DESC, score DESC",
                 "follow_up": "follow_up = '', follow_up ASC, score DESC"}.get(sort, "score DESC")
        order += ", id"                         # total order: stable "Load more" paging and identical results on every database
        sql_where = " AND ".join(where)
        cnt, rows = self._many([(f"SELECT COUNT(*) FROM leads WHERE {sql_where}", args),
                                (f"SELECT * FROM leads WHERE {sql_where} ORDER BY {order} LIMIT ? OFFSET ?",
                                 args + [limit, offset])])
        return [dict(r) for r in rows], cnt[0][0]

    def get(self, lead_id: str) -> dict | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        return dict(r) if r else None

    def find_by_prefix(self, prefix: str) -> dict | None:
        rows = self._many([("SELECT * FROM leads WHERE id LIKE ? LIMIT 2", (prefix.replace("%", "") + "%",))])[0]
        return dict(rows[0]) if len(rows) == 1 else None

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
        rows = self._many([("SELECT * FROM leads WHERE ai_verdict='' AND status='new' AND score BETWEEN ? AND ? "
                            "ORDER BY score DESC LIMIT ?", (lo, hi, limit))])[0]
        return [dict(r) for r in rows]

    def set_ai(self, lead_id: str, verdict: str, summary: str, score_delta: int = 0) -> None:
        d = int(score_delta)
        with self._tx() as c:
            c.execute("UPDATE leads SET ai_verdict=?, ai_summary=?, "
                      "score=CASE WHEN score+? > 100 THEN 100 WHEN score+? < 0 THEN 0 ELSE score+? END WHERE id=?",
                      (verdict, summary[:300], d, d, d, lead_id))

    def to_notify(self, min_score: int, limit: int = 20) -> list[dict]:
        rows = self._many([("SELECT * FROM leads WHERE notified=0 AND status='new' AND score>=? AND ai_verdict!='reject' "
                            "ORDER BY score DESC LIMIT ?", (min_score, limit))])[0]
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
            return [dict(r) for r in self._conn.execute("SELECT id, name, query FROM saved_filters ORDER BY name").fetchall()]

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
            (f"SELECT platform, COUNT(*) AS n FROM leads WHERE {live} GROUP BY platform ORDER BY n DESC, platform", ()),
            (f"SELECT CASE WHEN country='' THEN 'Unknown' ELSE country END AS k, COUNT(*) AS n FROM leads WHERE {live} GROUP BY k ORDER BY n DESC, k LIMIT 40", ()),
            ("SELECT status, COUNT(*) AS n FROM leads GROUP BY status", ()),
            (f"SELECT CASE WHEN language='' THEN 'Unknown' ELSE language END AS k, COUNT(*) AS n FROM leads WHERE {live} GROUP BY k ORDER BY n DESC, k", ()),
            (f"SELECT categories, substr(COALESCE(posted_at, found_at),1,10) AS d FROM leads WHERE {live}", ()),
            ("SELECT * FROM runs ORDER BY id DESC LIMIT 12", ()),
            (f"SELECT lead_type AS k, COUNT(*) AS n FROM leads WHERE {live} GROUP BY lead_type ORDER BY n DESC, k", ()),
            (f"SELECT CASE WHEN score>=90 THEN '90-100' WHEN score>=80 THEN '80-89' WHEN score>=70 THEN '70-79' "
             f"WHEN score>=60 THEN '60-69' WHEN score>=50 THEN '50-59' ELSE '35-49' END AS k, COUNT(*) AS n "
             f"FROM leads WHERE {live} GROUP BY k", ()),
            (f"SELECT substr(COALESCE(posted_at, found_at),1,10) AS k, COUNT(*) AS n FROM leads WHERE {live} "
             f"GROUP BY k ORDER BY k DESC LIMIT 30", ()),
            (f"SELECT budget_usd FROM leads WHERE budget_usd IS NOT NULL AND {live}", ()),
            ("SELECT platform, COUNT(*) AS leads, ROUND(AVG(score),1) AS avg_score, "
             "SUM(CASE WHEN status IN ('contacted','replied','won','lost') THEN 1 ELSE 0 END) AS worked, "
             "SUM(CASE WHEN status='replied' THEN 1 ELSE 0 END) AS replied, "
             "SUM(CASE WHEN status='won' THEN 1 ELSE 0 END) AS won, "
             "COALESCE(SUM(CASE WHEN status='won' THEN deal_value END),0) AS revenue "
             "FROM leads WHERE status!='ignored' GROUP BY platform ORDER BY leads DESC, platform", ()),
            ("SELECT COALESCE(SUM(deal_value),0) FROM leads WHERE status='won'", ()),
            ("SELECT COUNT(*) FROM leads WHERE follow_up!='' AND follow_up<=? AND status IN ('contacted','replied')", (today,)),
            (f"SELECT COUNT(*) FROM leads WHERE ai_verdict!='' AND {live}", ()),
        ])
        total, hot, with_contact = total[0][0], hot[0][0], with_contact[0][0]
        revenue, due, ai_n = float(revenue[0][0] or 0), due[0][0], ai_n[0][0]
        perf = [{**dict(r), "revenue": float(r["revenue"] or 0)} for r in perf]
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
            "platform_perf": perf,
            "category_trend": trend,
            "revenue": revenue, "follow_ups_due": due, "ai_reviewed": ai_n,
        }

    def unenriched(self, limit: int, min_score: int = 0) -> list[dict]:
        rows = self._many([("SELECT * FROM leads WHERE enriched=0 AND status='new' AND score>=? "
                            "AND source='tinyfish' ORDER BY score DESC LIMIT ?", (min_score, limit))])[0]
        return [dict(r) for r in rows]

    def for_site_enrichment(self, limit: int, min_score: int = 50) -> list[dict]:
        """Top new leads that have a company website we have not crawled yet."""
        rows = self._many([("SELECT * FROM leads WHERE status='new' AND score>=? AND details LIKE '%\"website\"%' "
                            "AND details NOT LIKE '%\"site\"%' ORDER BY score DESC, id LIMIT ?", (min_score, limit))])[0]
        return [dict(r) for r in rows]

    def _is_crosspost(self, c, lead: dict) -> bool:
        author, title = (lead.get("author") or "").strip(), (lead.get("title") or "").strip().lower()
        if not author or len(title) < 15:
            return False
        return c.execute("SELECT 1 FROM leads WHERE author=? AND lower(title)=? AND id!=? LIMIT 1",
                         (author, title, lead["id"])).fetchone() is not None

    def dedupe(self) -> int:
        """Remove later cross-posts (same author + same title, kept: the earliest found) that you have not worked yet."""
        with self._tx() as c:
            cur = c.execute("""DELETE FROM leads WHERE status='new' AND id IN (
                                 SELECT id FROM (
                                   SELECT id, ROW_NUMBER() OVER (PARTITION BY author, lower(title) ORDER BY found_at, id) AS rn
                                   FROM leads WHERE author!='' AND length(title)>=15) t
                                 WHERE rn>1)""")
            return cur.rowcount

    def delete(self, lead_id: str) -> None:
        with self._tx() as c:
            c.execute("DELETE FROM leads WHERE id=?", (lead_id,))

    def _copy_to(self, dst: sqlite3.Connection) -> None:
        """Copy the whole database into an open local SQLite connection (works for local files and Postgres)."""
        with self._lock:
            if not self.pg:
                self._conn.backup(dst)
                return
            dst.executescript(SCHEMA)
            have = {r[1] for r in dst.execute("PRAGMA table_info(leads)")}
            for col, ddl in MIGRATIONS.items():
                if col not in have:
                    dst.execute(f"ALTER TABLE leads ADD COLUMN {col} {ddl}")
            for table in _TABLES:
                rows = self._many([(f"SELECT * FROM {table}", ())])[0]
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
            w.writerow({k: csv_safe(v) for k, v in r.items()})      # neutralise spreadsheet formulas (=, +, -, @)
        return buf.getvalue()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def open_store(cfg: dict, local: bool = False) -> Store:
    """Neon (Postgres) when DATABASE_URL is set, otherwise the local SQLite file."""
    url = database_url()
    if url and not local:
        return Store(url)
    return Store(cfg["database"])


def _upsert_sql(dst: Store, table: str, cols: list[str], conflict: list[str]) -> str:
    ph = ",".join("?" * len(cols))
    if dst.pg:
        sets = ",".join(f"{c}=excluded.{c}" for c in cols if c not in conflict)
        return (f"INSERT INTO {table} ({','.join(cols)}) VALUES ({ph}) "
                f"ON CONFLICT ({','.join(conflict)}) DO UPDATE SET {sets}")
    return f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({ph})"


def migrate(src: Store, dst: Store, batch_size: int = 100) -> dict:
    """Copy every table from one store to another (e.g. local SQLite -> Neon). Safe to re-run."""
    counts = {}
    plan = {"leads": ["id"], "saved_filters": ["name"], "meta": ["key"], "runs": None}
    for table, conflict in plan.items():
        rows = src._many([(f"SELECT * FROM {table}", ())])[0]
        counts[table] = len(rows)
        if not rows:
            continue
        cols = [c for c in rows[0].keys() if not (c == "id" and table in ("runs", "saved_filters"))]
        if conflict is None:
            if dst._many([("SELECT COUNT(*) FROM runs", ())])[0][0][0]:
                continue                                    # keep the destination's own run history
            sql = f"INSERT INTO runs ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
        else:
            sql = _upsert_sql(dst, table, cols, conflict)
        for i in range(0, len(rows), batch_size):
            chunk = [(sql, tuple(r[c] for c in cols)) for r in rows[i:i + batch_size]]
            if dst.pg:
                dst._conn.batch(chunk, atomic=True)
            else:
                with dst._tx() as c:
                    for q, a in chunk:
                        c.execute(q, a)
    return counts
