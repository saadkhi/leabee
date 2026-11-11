"""Sessions and two-factor login (standard library only).

* Session cookie: HMAC-SHA256-signed payload {expiry, csrf token, browser fingerprint}. HttpOnly, SameSite=Strict, Secure on HTTPS (with the
  `__Host-` prefix there). The signing key is derived from your password (hash) and TOTP secret, so changing either logs everyone out.
* Two-factor: optional TOTP (RFC 6238, works with Google Authenticator, Authy, 1Password, Aegis...). Codes cannot be replayed.
* HTTP Basic is only accepted when LEABEE_ALLOW_BASIC=1 and no TOTP secret is set (for scripts)."""
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
def signing_key(plain: str, hashed: str, totp_secret: str) -> bytes:
    """Derived from the credentials: rotating the password or the TOTP secret invalidates every session."""
    material = f"{hashed or plain}|{totp_secret}|{os.environ.get('LEABEE_SESSION_SALT', '')}".encode()
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


# ------------------------------------------------------------------------------------------------ TOTP (RFC 6238)
def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(secret_b32: str, counter: int, digits: int = 6) -> str:
    key = base64.b32decode(secret_b32.upper() + "=" * (-len(secret_b32) % 8))
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = h[-1] & 0x0F
    return str((struct.unpack(">I", h[off:off + 4])[0] & 0x7FFFFFFF) % 10 ** digits).zfill(digits)


def totp(secret_b32: str, at: float | None = None, step: int = 30) -> str:
    return _hotp(secret_b32, int((at or time.time()) // step))


def verify_totp(secret_b32: str, code: str, last_step: int = 0, at: float | None = None, step: int = 30, window: int = 1) -> tuple[bool, int]:
    """(ok, step used). Accepts the previous/next 30 s window for clock drift; a step at or below `last_step` is a replay and is refused."""
    code = (code or "").strip().replace(" ", "")
    if not (code.isdigit() and len(code) == 6):
        return False, last_step
    now_step = int((at or time.time()) // step)
    for s in range(now_step - window, now_step + window + 1):
        if s > last_step and hmac.compare_digest(_hotp(secret_b32, s), code):
            return True, s
    return False, last_step


def otpauth_uri(secret_b32: str, account: str = "admin", issuer: str = "Leabee") -> str:
    from urllib.parse import quote
    return f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret_b32}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"


def totp_secret() -> str:
    return os.environ.get("LEABEE_TOTP_SECRET", "").strip().replace(" ", "")
