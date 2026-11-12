"""Sessions for the ID + password login (standard library only).

* Session cookie: HMAC-SHA256-signed payload {expiry, csrf token, browser fingerprint}. HttpOnly, SameSite=Strict, Secure on HTTPS (with the
  `__Host-` prefix there). The signing key is derived from your ID and password (hash), so changing either logs everyone out.
* HTTP Basic is only accepted when LEABEE_ALLOW_BASIC=1 (for scripts)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import time

SESSION_TTL = 12 * 3600
COOKIE = "leabee_session"
COOKIE_SECURE = "__Host-leabee_session"


# ------------------------------------------------------------------------------------------------ sessions
def signing_key(plain: str, hashed: str, user: str = "") -> bytes:
    """Derived from the credentials: rotating the password or the ID invalidates every session."""
    material = f"{hashed or plain}|{user}|{os.environ.get('LEABEE_SESSION_SALT', '')}".encode()
    return hmac.new(b"leabee-session-v1", material, hashlib.sha256).digest()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def fingerprint(user_agent: str) -> str:
    return hashlib.sha256((user_agent or "").encode()).hexdigest()[:16]


def make_session(key: bytes, user_agent: str = "", ttl: int = SESSION_TTL, now: float | None = None) -> tuple[str, str]:
    """Return (cookie value, csrf token)."""
    csrf = secrets.token_urlsafe(24)
    payload = {"exp": int((now or time.time()) + ttl), "csrf": csrf, "ua": fingerprint(user_agent), "sid": secrets.token_urlsafe(8)}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64(hmac.new(key, body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}", csrf


def read_session(key: bytes, value: str, user_agent: str = "", now: float | None = None) -> dict | None:
    try:
        body, sig = (value or "").split(".")
        good = _b64(hmac.new(key, body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return None
        p = json.loads(_unb64(body))
        if p["exp"] < (now or time.time()) or not hmac.compare_digest(p["ua"], fingerprint(user_agent)):
            return None
        return p
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


# ------------------------------------------------------------------------------------------------ username
def username() -> str:
    """The login ID (LEABEE_USERNAME, default "admin")."""
    return os.environ.get("LEABEE_USERNAME", "").strip()[:64] or "admin"


def username_ok(given: str) -> bool:
    return hmac.compare_digest(str(given or "").strip().encode(), username().encode())
