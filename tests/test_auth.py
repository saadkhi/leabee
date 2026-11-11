"""Sessions, CSRF tokens, TOTP 2FA, persistent lockout, audit log, encryption at rest, tighter CSP. All data is invented."""
import base64
import time

import pytest

from authutil import PW, authed, csrf_of, login
from leabee import auth, crypto
from leabee import security as sec
from leabee.storage import Store
from leabee.web import create_app

CFG = {"min_score": 35, "max_age_days": 10 ** 5, "hot_score": 70}


def _lead(i, **kw):
    d = dict(id=i, source="reddit", platform="Reddit", lead_type="buyer", title=f"lead {i} title long", text="x", url=f"https://u/{i}", author="", author_url="",
             posted_at="2099-01-01T00:00:00+00:00", categories="automation", country="", budget_text="", budget_usd=None, contact="", score=70, reasons="",
             enriched=0, language="en", grade="B", kind="gig", next_action="")
    d.update(kw)
    return d


@pytest.fixture
def secured(monkeypatch):
    for v in ("LEABEE_PASSWORD_HASH", "LEABEE_ALLOWED_HOSTS", "VERCEL", "LEABEE_TRUST_PROXY", "LEABEE_TOTP_SECRET", "LEABEE_ALLOW_BASIC", "LEABEE_DATA_KEY"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("LEABEE_PASSWORD", PW)
    s = Store(":memory:")
    s.upsert(_lead("abc12345"))
    return create_app(CFG, s), s


# ----------------------------------------------------------------------------- session login
def test_session_login_logout_and_csrf_token(secured):
    app, store = secured
    c = app.test_client()
    r = c.get("/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/login")                  # pages redirect to the login form
    assert c.get("/api/stats").status_code == 401 and c.get("/export.csv").status_code == 401  # APIs answer 401, never data
    assert "WWW-Authenticate" not in c.get("/api/stats").headers                                # no Basic prompt by default
    page = c.get("/login")
    assert page.status_code == 200 and b"Authenticator code" not in page.data and b'type="password"' in page.data
    bad = login(c, "wrong-password-12345")
    assert bad.status_code == 401 and bad.get_json() == {"error": "Invalid credentials"}
    ok = login(c)
    cookie = ok.headers["Set-Cookie"]
    assert ok.status_code == 200 and "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Secure" not in cookie     # no Secure over plain http
    assert c.get("/api/stats").status_code == 200 and c.get("/").status_code == 200 and c.get("/login").status_code == 302
    token = csrf_of(c)
    assert len(token) >= 20
    assert c.patch("/api/leads/abc12345", json={"status": "contacted"}).status_code == 403                                  # no token
    assert c.patch("/api/leads/abc12345", json={"status": "contacted"}, headers={"X-CSRF-Token": "x" * len(token)}).status_code == 403
    assert c.patch("/api/leads/abc12345", json={"status": "contacted"}, headers={"X-CSRF-Token": token}).status_code == 200
    out = c.post("/logout", json={})
    assert out.headers["Clear-Site-Data"] and c.get("/api/stats").status_code == 401                                        # session is gone


def test_secure_cookie_name_on_https(secured):
    app, _ = secured
    c = app.test_client()
    r = login(c, headers={"X-Forwarded-Proto": "https"})
    assert r.headers["Set-Cookie"].startswith("__Host-leabee_session=") and "Secure" in r.headers["Set-Cookie"]


def test_session_cookie_is_tamper_proof_and_bound(monkeypatch):
    k = auth.signing_key(PW, "", "")
    val, csrf = auth.make_session(k, "Mozilla/5.0 A", now=1000)
    assert auth.read_session(k, val, "Mozilla/5.0 A", now=1001)["csrf"] == csrf
    assert auth.read_session(k, val, "Mozilla/5.0 B", now=1001) is None                      # stolen cookie in another browser
    assert auth.read_session(k, val, "Mozilla/5.0 A", now=1000 + auth.SESSION_TTL + 1) is None   # expired
    body, sig = val.split(".")
    forged = auth._b64(b'{"exp":9999999999,"csrf":"x","ua":"' + auth.fingerprint("Mozilla/5.0 A").encode() + b'","sid":"s"}')
    assert auth.read_session(k, f"{forged}.{sig}", "Mozilla/5.0 A", now=1001) is None        # edited payload
    assert auth.read_session(k, f"{body}.{sig[:-2]}AA", "Mozilla/5.0 A", now=1001) is None and auth.read_session(k, "garbage", "") is None
    assert auth.read_session(auth.signing_key("another-password-12", "", ""), val, "Mozilla/5.0 A", now=1001) is None   # new password logs everyone out


# ----------------------------------------------------------------------------- 2FA
def test_totp_rfc_vectors_and_replay():
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert auth._hotp(secret, 59 // 30) == "287082" and auth._hotp(secret, 1111111109 // 30) == "081804"          # RFC 6238 vectors (6 digits)
    now = 1_700_000_000
    code = auth.totp(secret, now)
    ok, step = auth.verify_totp(secret, code, 0, at=now)
    assert ok and step == now // 30
    assert auth.verify_totp(secret, code, step, at=now) == (False, step)                      # the same code cannot be used twice
    assert auth.verify_totp(secret, auth.totp(secret, now - 30), 0, at=now)[0]                # one step of clock drift is fine
    assert not auth.verify_totp(secret, auth.totp(secret, now - 120), 0, at=now)[0]
    assert not auth.verify_totp(secret, "12345", 0, at=now)[0] and not auth.verify_totp(secret, "abcdef", 0, at=now)[0] and not auth.verify_totp(secret, "", 0)[0]
    assert auth.otpauth_uri("ABC", "me").startswith("otpauth://totp/Leabee:me?secret=ABC")
    assert len(auth.new_totp_secret()) >= 32


def test_login_requires_the_authenticator_code(secured, monkeypatch):
    app, store = secured
    secret = auth.new_totp_secret()
    monkeypatch.setenv("LEABEE_TOTP_SECRET", secret)
    monkeypatch.setenv("LEABEE_ALLOW_BASIC", "1")                                             # even then, Basic is refused with 2FA on
    a2 = create_app(CFG, store)
    c = a2.test_client()
    assert b"Authenticator code" in c.get("/login").data
    assert login(c).status_code == 401 and login(c, code="000000").status_code == 401          # password alone is not enough
    assert login(c, "wrong-password-12345", auth.totp(secret)).status_code == 401              # nor is a code alone
    code = auth.totp(secret)
    assert login(c, code=code).status_code == 200
    assert login(a2.test_client(), code=code).status_code == 401                               # replay of the same code is refused
    basic = {"Authorization": "Basic " + base64.b64encode(f"u:{PW}".encode()).decode()}
    assert a2.test_client().get("/api/stats", headers=basic).status_code == 401


# ----------------------------------------------------------------------------- lockout (database-backed) and Basic fallback
def test_lockout_is_stored_in_the_database_and_survives_a_new_instance(secured):
    app, store = secured
    c = app.test_client()
    assert [login(c, "wrong-password-12345").status_code for _ in range(8)] == [401] * 8
    locked = login(c)
    assert locked.status_code == 429 and locked.headers["Retry-After"]                         # right password refused while locked
    assert store.count_events("login_fail", 900) == 8
    fresh = create_app(CFG, store).test_client()                                               # a different serverless instance, same database
    assert login(fresh).status_code == 429
    assert store.events(1, "lockout")


def test_basic_auth_only_when_explicitly_allowed(secured, monkeypatch):
    app, store = secured
    basic = {"Authorization": "Basic " + base64.b64encode(f"u:{PW}".encode()).decode()}
    assert app.test_client().get("/api/stats", headers=basic).status_code == 401              # off by default
    monkeypatch.setenv("LEABEE_ALLOW_BASIC", "1")
    a2 = create_app(CFG, store)
    c = a2.test_client()
    assert c.get("/api/stats", headers=basic).status_code == 200 and "WWW-Authenticate" in c.get("/api/stats").headers
    bad = {"Authorization": "Basic " + base64.b64encode(b"u:wrong-password-xx").decode()}
    assert [c.get("/api/stats", headers=bad).status_code for _ in range(8)] == [401] * 8
    assert c.get("/api/stats", headers=basic).status_code == 429                               # Basic attempts feed the same lockout


# ----------------------------------------------------------------------------- audit log
def test_audit_log_records_security_events_without_secrets(secured):
    app, store = secured
    c = app.test_client()
    login(c, "wrong-password-12345")
    c, h = authed(c)
    c.get("/export.csv")
    c.post("/api/leads/bulk", json={"ids": ["abc12345"], "status": "won"}, headers=h)
    c.post("/logout", json={}, headers=h)
    names = [e["event"] for e in store.events(20)]
    assert names[:3] == ["logout", "bulk_status", "export"] and {"login_fail", "login_ok"} <= set(names)
    store.log_event("test", "1.2.3.4", "key sk-abcdefghijklmnopqrstuvwxyz123456 and postgresql://u:hunter2pass@db.neon.tech/x")   # leabee:allow-secret
    last = store.events(1)[0]
    assert "hunter2pass" not in last["detail"] and "sk-abcdef" not in last["detail"]
    assert store.events(5, "login_fail")[0]["ip"]


# ----------------------------------------------------------------------------- encryption at rest
def test_notes_are_encrypted_in_the_database_and_readable_through_the_app(monkeypatch):
    monkeypatch.setenv("LEABEE_DATA_KEY", crypto.generate_key())
    s = Store(":memory:")
    s.upsert(_lead("n1"))
    s.update_user_fields("n1", notes="call Sam on +1 919 555 0100, budget is flexible")
    raw = s._conn.execute("SELECT notes FROM leads WHERE id='n1'").fetchone()[0]
    assert raw.startswith("enc:v1:") and "Sam" not in raw and "919" not in raw
    assert s.get("n1")["notes"].startswith("call Sam") and s.query()[0][0]["notes"].startswith("call Sam") and "call Sam" in s.to_csv(s.query()[0])
    other = crypto.generate_key()
    monkeypatch.setenv("LEABEE_DATA_KEY", other)
    assert "wrong LEABEE_DATA_KEY" in s.get("n1")["notes"]
    monkeypatch.delenv("LEABEE_DATA_KEY")
    assert "set LEABEE_DATA_KEY" in s.get("n1")["notes"]
    s.update_user_fields("n1", notes="plain after key removed")
    assert s._conn.execute("SELECT notes FROM leads WHERE id='n1'").fetchone()[0] == "plain after key removed"       # old plaintext stays readable


def test_backups_are_encrypted_when_a_key_is_set(monkeypatch, tmp_path):
    s = Store(str(tmp_path / "leads.db"))
    s.upsert(_lead("b1", title="secret client project title"))
    plain = s.backup(str(tmp_path / "bk1"))
    assert plain.endswith(".db") and b"SQLite format" in open(plain, "rb").read(16)
    monkeypatch.setenv("LEABEE_DATA_KEY", crypto.generate_key())
    enc = s.backup(str(tmp_path / "bk2"))
    data = open(enc, "rb").read()
    assert enc.endswith(".db.enc") and b"secret client project" not in data and b"SQLite" not in data
    assert not list((tmp_path / "bk2").glob("*.db"))                                           # the plaintext copy is removed
    back = crypto.decrypt_file(enc, tmp_path / "restored.db")
    assert Store(back).get("b1")["title"] == "secret client project title"


# ----------------------------------------------------------------------------- CSP and headers
def test_csp_styles_use_a_nonce_and_inline_style_is_attribute_only(secured):
    app, _ = secured
    c, _h = authed(app.test_client())
    r = c.get("/")
    pol = r.headers["Content-Security-Policy"]
    nonce = pol.split("'nonce-")[1].split("'")[0]
    assert f"style-src 'self' 'nonce-{nonce}'" in pol and "style-src-attr 'unsafe-inline'" in pol
    assert pol.count("unsafe-inline") == 1                                                      # only for style attributes, never for scripts or <style>
    html = r.get_data(as_text=True)
    assert f'<style nonce="{nonce}">' in html and "<style>" not in html and 'name="csrf-token"' in html
    assert 'id="logout"' in html


def test_unprotected_local_mode_needs_no_login(monkeypatch):
    for v in ("LEABEE_PASSWORD", "LEABEE_PASSWORD_HASH", "LEABEE_TOTP_SECRET"):
        monkeypatch.delenv(v, raising=False)
    c = create_app(CFG, Store(":memory:")).test_client()
    assert c.get("/").status_code == 200 and c.get("/login").status_code == 302 and 'id="logout"' not in c.get("/").get_data(as_text=True)
    assert c.patch("/api/leads/none", json={"status": "won"}).status_code == 404                # writes work without a token when there is no session
