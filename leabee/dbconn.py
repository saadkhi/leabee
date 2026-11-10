"""Postgres (Neon) connection with the small sqlite3-like surface that Store uses. Needs `psycopg` (v3).

Notes for Neon on serverless: use the *pooled* connection string (host contains "-pooler"). Prepared statements are disabled
(PgBouncer transaction mode), every statement auto-commits, and a dropped idle connection is reopened once transparently.
"""
from __future__ import annotations

from contextlib import nullcontext
from decimal import Decimal


class Row:
    """Row supporting r["col"], r[0], keys() (so dict(row) works), iteration and len()."""
    __slots__ = ("_cols", "_vals")

    def __init__(self, cols: list[str], vals: list):
        self._cols, self._vals = cols, vals

    def keys(self) -> list[str]:
        return list(self._cols)

    def __getitem__(self, k):
        return self._vals[self._cols.index(k)] if isinstance(k, str) else self._vals[k]

    def __iter__(self):
        return iter(self._vals)

    def __len__(self):
        return len(self._vals)

    def __repr__(self):
        return f"Row({dict(zip(self._cols, self._vals))})"


class Cursor:
    def __init__(self, rows: list[Row], rowcount: int = -1):
        self._rows, self.rowcount, self._i = rows, rowcount, 0

    def fetchone(self):
        if self._i < len(self._rows):
            self._i += 1
            return self._rows[self._i - 1]
        return None

    def fetchall(self):
        out, self._i = self._rows[self._i:], len(self._rows)
        return out

    def __iter__(self):
        return iter(self.fetchall())


def translate(sql: str) -> str:
    """Store writes sqlite-style `?` placeholders; psycopg wants `%s` (and literal % doubled)."""
    return sql.replace("%", "%%").replace("?", "%s")


def _norm(v):
    return float(v) if isinstance(v, Decimal) else v


class PgConnection:
    row_factory = None      # sqlite3 parity only

    def __init__(self, dsn: str, connect_timeout: int = 10):
        if not dsn:
            raise ValueError("DATABASE_URL is empty")
        import psycopg      # imported lazily so SQLite-only installs never need it
        self._pg = psycopg
        self.dsn, self.connect_timeout = dsn, connect_timeout
        self._conn = None

    def _get(self):
        if self._conn is None or self._conn.closed:
            extra = {} if "connect_timeout" in self.dsn else {"connect_timeout": self.connect_timeout}
            self._conn = self._pg.connect(self.dsn, autocommit=True, prepare_threshold=None, **extra)
        return self._conn

    def _drop(self) -> None:
        try:
            if self._conn is not None:
                self._conn.close()
        except Exception:
            pass
        self._conn = None

    @staticmethod
    def _wrap(cur) -> Cursor:
        if cur.description is None:
            return Cursor([], cur.rowcount)
        cols = [d.name for d in cur.description]
        return Cursor([Row(cols, [_norm(v) for v in r]) for r in cur.fetchall()], cur.rowcount)

    def _run(self, stmts: list[tuple[str, tuple]], atomic: bool) -> list[Cursor]:
        for attempt in (1, 2):
            fresh = self._conn is None or self._conn.closed
            try:
                conn = self._get()
                with (conn.transaction() if atomic else nullcontext()):
                    return [self._wrap(conn.execute(translate(sql), tuple(args))) for sql, args in stmts]
            except (self._pg.OperationalError, self._pg.InterfaceError):
                self._drop()            # Neon closes idle connections: reconnect once. A brand-new connection that
                if attempt == 2 or fresh:   # fails (wrong host, Neon down) is not retried, so errors surface fast
                    raise
        raise RuntimeError("unreachable")

    # -- sqlite3-like surface ---------------------------------------------
    def execute(self, sql: str, params=()) -> Cursor:
        return self._run([(sql, tuple(params))], False)[0]

    def batch(self, stmts, atomic: bool = False) -> list[Cursor]:
        stmts = [(s, tuple(a)) for s, a in stmts]
        return self._run(stmts, atomic) if stmts else []

    def executescript(self, script: str) -> None:
        self._run([(s.strip(), ()) for s in script.split(";") if s.strip()], False)

    def commit(self) -> None:       # autocommit
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        self._drop()
