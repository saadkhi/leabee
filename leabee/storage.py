from __future__ import annotations

import csv
import io
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

COLUMNS = ["id", "source", "platform", "lead_type", "title", "text", "url", "author", "author_url",
           "posted_at", "found_at", "categories", "country", "budget_text", "budget_usd", "contact",
           "score", "status", "notes", "enriched", "reasons"]

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
"""

STATUSES = ("new", "contacted", "replied", "won", "lost", "ignored")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=60)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
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

    def upsert(self, lead: dict) -> bool:
        """Insert or refresh a lead. Returns True if it is new. Keeps user status/notes."""
        lead = dict(lead)
        lead.setdefault("found_at", now_iso())
        with self._tx() as c:
            row = c.execute("SELECT score, enriched FROM leads WHERE id=?", (lead["id"],)).fetchone()
            if row is None:
                if self._is_crosspost(c, lead):
                    return False
                cols = [k for k in COLUMNS if k in lead]
                c.execute(f"INSERT INTO leads ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                          [lead[k] for k in cols])
                return True
            if row["enriched"] and not lead.get("enriched"):
                # never overwrite richer enriched text with a snippet
                return False
            c.execute("""UPDATE leads SET title=?, text=?, categories=?, country=?, budget_text=?, budget_usd=?,
                         contact=?, score=?, reasons=?, enriched=MAX(enriched, ?) WHERE id=?""",
                      (lead["title"], lead["text"], lead["categories"], lead["country"], lead["budget_text"],
                       lead["budget_usd"], lead["contact"], lead["score"], lead.get("reasons", ""),
                       int(lead.get("enriched", 0)), lead["id"]))
            return False

    def log_run(self, source: str, started: str, fetched: int, kept: int, new: int, error: str = "") -> None:
        with self._tx() as c:
            c.execute("INSERT INTO runs(started_at, finished_at, source, fetched, kept, new, error) VALUES (?,?,?,?,?,?,?)",
                      (started, now_iso(), source, fetched, kept, new, error))

    def query(self, *, min_score: int = 0, status: str = "", category: str = "", country: str = "",
              platform: str = "", lead_type: str = "", q: str = "", has_contact: bool = False,
              since_hours: float = 0, sort: str = "score", limit: int = 200, offset: int = 0,
              exclude_ignored: bool = True) -> tuple[list[dict], int]:
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
        if has_contact:
            where.append("contact != ''")
        if q:
            where.append("(title LIKE ? OR text LIKE ? OR author LIKE ?)")
            args += [f"%{q}%"] * 3
        if since_hours:
            cutoff = datetime.now(timezone.utc).timestamp() - since_hours * 3600
            where.append("CAST(strftime('%s', COALESCE(posted_at, found_at)) AS INTEGER) >= ?")
            args.append(int(cutoff))
        order = {"score": "score DESC, COALESCE(posted_at, found_at) DESC",
                 "new": "COALESCE(posted_at, found_at) DESC",
                 "budget": "budget_usd IS NULL, budget_usd DESC, score DESC"}.get(sort, "score DESC")
        sql_where = " AND ".join(where)
        with self._lock:
            total = self._conn.execute(f"SELECT COUNT(*) FROM leads WHERE {sql_where}", args).fetchone()[0]
            rows = self._conn.execute(
                f"SELECT * FROM leads WHERE {sql_where} ORDER BY {order} LIMIT ? OFFSET ?",
                args + [limit, offset]).fetchall()
        return [dict(r) for r in rows], total

    def get(self, lead_id: str) -> dict | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        return dict(r) if r else None

    def update_user_fields(self, lead_id: str, status: str | None = None, notes: str | None = None) -> bool:
        if status is not None and status not in STATUSES:
            raise ValueError(f"invalid status: {status}")
        with self._tx() as c:
            if status is not None:
                c.execute("UPDATE leads SET status=? WHERE id=?", (status, lead_id))
            if notes is not None:
                c.execute("UPDATE leads SET notes=? WHERE id=?", (notes[:5000], lead_id))
            return c.execute("SELECT 1 FROM leads WHERE id=?", (lead_id,)).fetchone() is not None

    def stats(self) -> dict:
        with self._lock:
            c = self._conn
            live = "status != 'ignored'"
            total = c.execute(f"SELECT COUNT(*) FROM leads WHERE {live}").fetchone()[0]
            hot = c.execute(f"SELECT COUNT(*) FROM leads WHERE score>=70 AND {live}").fetchone()[0]
            with_contact = c.execute(f"SELECT COUNT(*) FROM leads WHERE contact!='' AND {live}").fetchone()[0]
            by_platform = c.execute(f"SELECT platform, COUNT(*) n FROM leads WHERE {live} GROUP BY platform ORDER BY n DESC").fetchall()
            by_country = c.execute(f"SELECT CASE WHEN country='' THEN 'Unknown' ELSE country END k, COUNT(*) n FROM leads WHERE {live} GROUP BY k ORDER BY n DESC LIMIT 40").fetchall()
            by_status = c.execute("SELECT status, COUNT(*) n FROM leads GROUP BY status").fetchall()
            cat_rows = c.execute(f"SELECT categories FROM leads WHERE {live}").fetchall()
            last = c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 12").fetchall()
        cats: dict[str, int] = {}
        for r in cat_rows:
            for k in filter(None, (r[0] or "").split(",")):
                cats[k] = cats.get(k, 0) + 1
        return {
            "total": total, "hot": hot, "with_contact": with_contact,
            "by_platform": [dict(r) for r in by_platform],
            "by_country": [dict(r) for r in by_country],
            "by_status": [dict(r) for r in by_status],
            "by_category": sorted(({"k": k, "n": n} for k, n in cats.items()), key=lambda x: -x["n"]),
            "runs": [dict(r) for r in last],
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

    def to_csv(self, rows: list[dict]) -> str:
        buf = io.StringIO()
        cols = ["score", "status", "platform", "lead_type", "title", "url", "author", "author_url", "country",
                "categories", "budget_text", "budget_usd", "contact", "posted_at", "found_at", "notes", "text"]
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
