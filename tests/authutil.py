"""Shared helpers for tests that need a logged-in client."""
import re

PW = "correct-horse-battery"


def login(client, password=PW, username="admin", headers=None):
    r = client.post("/login", json={"username": username, "password": password}, headers=headers or {})
    return r


def csrf_of(client) -> str:
    html = client.get("/").get_data(as_text=True)
    m = re.search(r'name="csrf-token" content="([^"]+)"', html)
    return m.group(1) if m else ""


def authed(client, password=PW):
    """Log in and return (client, headers) where headers carry the CSRF token for write requests."""
    assert login(client, password).status_code == 200
    return client, {"X-CSRF-Token": csrf_of(client)}
