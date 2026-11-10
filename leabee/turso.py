"""Minimal sqlite3-style connection over Turso's HTTP API (Hrana "pipeline"), using only `requests`.

Turso is hosted SQLite with a free tier, so the same SQL works. This lets the dashboard on Vercel, GitHub Actions and your
PC all share one persistent database. Only the sqlite3 surface that Store uses is implemented.
"""
from __future__ import annotations

import base64
import time

import requests


class TursoError(RuntimeError):
    pass


class TRow:
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
        return f"TRow({dict(zip(self._cols, self._vals))})"


class TCursor:
    def __init__(self, rows: list[TRow], rowcount: int = -1):
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


def _enc(v) -> dict:
    if v is None:
        return {"type": "null"}
    if isinstance(v, bool):
        return {"type": "integer", "value": str(int(v))}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": v}
    if isinstance(v, (bytes, bytearray)):
        return {"type": "blob", "base64": base64.b64encode(bytes(v)).decode()}
    return {"type": "text", "value": str(v)}


def _dec(cell: dict):
    t = cell.get("type")
    if t == "null":
        return None
    if t == "integer":
        return int(cell["value"])
    if t == "float":
        return float(cell["value"])
    if t == "blob":
        return base64.b64decode(cell.get("base64", ""))
    return cell.get("value")


class TursoConnection:
    row_factory = None      # accepted for sqlite3 parity; rows are always TRow

    def __init__(self, url: str, token: str, session: requests.Session | None = None, timeout: float = 40, retries: int = 4):
        if not url or not token:
            raise TursoError("TURSO_DATABASE_URL and TURSO_AUTH_TOKEN are required")
        self.base = url.strip().replace("libsql://", "https://").replace("wss://", "https://").rstrip("/")
        self.token, self.timeout, self.retries = token.strip(), timeout, retries
        self.session = session or requests.Session()

    # -- transport -------------------------------------------------------
    def _pipeline(self, stmts: list[tuple[str, tuple]]) -> list[TCursor]:
        reqs = [{"type": "execute", "stmt": {"sql": sql, "args": [_enc(a) for a in args]}} for sql, args in stmts]
        reqs.append({"type": "close"})
        last = ""
        for attempt in range(1, self.retries + 1):
            try:
                r = self.session.post(f"{self.base}/v2/pipeline", json={"requests": reqs}, timeout=self.timeout,
                                      headers={"Authorization": f"Bearer {self.token}"})
            except requests.RequestException as e:
                last = f"{e.__class__.__name__}: {e}"
                time.sleep(min(2 * attempt, 8))
                continue
            if r.status_code in (429, 502, 503, 504):
                last = f"HTTP {r.status_code}"
                time.sleep(min(2 * attempt, 8))
                continue
            if r.status_code != 200:
                raise TursoError(f"Turso HTTP {r.status_code}: {r.text[:300]}")
            out: list[TCursor] = []
            for res in r.json().get("results", [])[:len(stmts)]:
                if res.get("type") == "error":
                    raise TursoError(res.get("error", {}).get("message", "unknown Turso error"))
                result = res["response"]["result"]
                cols = [c.get("name") or "" for c in result.get("cols", [])]
                rows = [TRow(cols, [_dec(c) for c in row]) for row in result.get("rows", [])]
                out.append(TCursor(rows, int(result.get("affected_row_count", -1) or 0)))
            if len(out) != len(stmts):
                raise TursoError("Turso returned fewer results than statements")
            return out
        raise TursoError(f"Turso unreachable after {self.retries} attempts ({last})")

    # -- sqlite3-like surface ---------------------------------------------
    def execute(self, sql: str, params=()) -> TCursor:
        return self._pipeline([(sql, tuple(params))])[0]

    def batch(self, stmts: list[tuple[str, tuple]], atomic: bool = False) -> list[TCursor]:
        """Many statements in ONE HTTP round trip. atomic=True wraps them in BEGIN/COMMIT."""
        stmts = [(s, tuple(a)) for s, a in stmts]
        if not stmts:
            return []
        if atomic:
            res = self._pipeline([("BEGIN", ())] + stmts + [("COMMIT", ())])
            return res[1:-1]
        return self._pipeline(stmts)

    def executescript(self, script: str) -> None:
        stmts = [(s.strip(), ()) for s in script.split(";") if s.strip()]
        self._pipeline(stmts)

    def commit(self) -> None:       # every statement auto-commits
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        self.session.close()
