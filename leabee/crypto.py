"""Optional encryption at rest for the most personal data (your notes) and for backup files, using Fernet (AES-128-CBC + HMAC-SHA256)
from the open-source `cryptography` package. Enabled by setting LEABEE_DATA_KEY (create one with `python -m leabee gen-data-key`).

Keep the key OUTSIDE the database and the repo (a password manager, Vercel / GitHub secrets). Lose the key and encrypted notes and backups
cannot be recovered. Plain-text values written before the key was set stay readable."""
from __future__ import annotations

import os
from pathlib import Path

PREFIX = "enc:v1:"


def _fernet():
    k = os.environ.get("LEABEE_DATA_KEY", "").strip()
    if not k:
        return None
    from cryptography.fernet import Fernet
    return Fernet(k.encode())


def enabled() -> bool:
    return bool(os.environ.get("LEABEE_DATA_KEY", "").strip())


def generate_key() -> str:
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


def encrypt(text: str | None) -> str | None:
    if not text:
        return text
    f = _fernet()
    if f is None or text.startswith(PREFIX):
        return text
    return PREFIX + f.encrypt(text.encode()).decode()


def decrypt(text: str | None) -> str | None:
    if not text or not text.startswith(PREFIX):
        return text
    f = _fernet()
    if f is None:
        return "[encrypted: set LEABEE_DATA_KEY to read this]"
    from cryptography.fernet import InvalidToken
    try:
        return f.decrypt(text[len(PREFIX):].encode()).decode()
    except InvalidToken:
        return "[cannot decrypt: wrong LEABEE_DATA_KEY]"


def encrypt_file(src: str | Path, dst: str | Path | None = None, remove_source: bool = True) -> str:
    f = _fernet()
    if f is None:
        raise RuntimeError("LEABEE_DATA_KEY is not set")
    src = Path(src)
    out = Path(dst) if dst else src.with_suffix(src.suffix + ".enc")
    out.write_bytes(f.encrypt(src.read_bytes()))
    if remove_source:
        src.unlink(missing_ok=True)
    return str(out)


def decrypt_file(src: str | Path, dst: str | Path) -> str:
    f = _fernet()
    if f is None:
        raise RuntimeError("LEABEE_DATA_KEY is not set")
    Path(dst).write_bytes(f.decrypt(Path(src).read_bytes()))
    return str(dst)
