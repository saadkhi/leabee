from leabee import digest, notify, telegram_bot
from leabee.storage import Store


def _lead(i, score=70, **kw):
    d = dict(id=i * 8, source="reddit", platform="Reddit", lead_type="buyer", title=f"lead number {i} title long", text="x", url=f"https://u/{i}",
             author="", author_url="", posted_at="2099-01-01T00:00:00+00:00", categories="automation", country="", budget_text="", budget_usd=None,
             contact="", score=score, reasons="", enriched=0, language="en", grade="B", next_action="Reply in the thread")
    d.update(kw)
    return d


class Api:
    def __init__(self): self.calls = []
    def call(self, method, **p): self.calls.append((method, p)); return {}


def test_buttons_and_alert_per_lead(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t"); monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False); monkeypatch.delenv("SMTP_HOST", raising=False)
    s = Store(":memory:")
    s.upsert(_lead("a", 90)); s.baseline_notified(0); s.set_meta("alerts_baselined", "1")
    s.upsert(_lead("b", 95)); s.upsert(_lead("c", 85))
    sent = []
    monkeypatch.setattr(notify, "_telegram", lambda text, markup=None: sent.append((text, markup)))
    res = notify.alert_new_hot({"alerts": {"min_score": 80}}, s)
    assert res["sent"] == 2 and len(sent) == 2                                    # one message per lead
    kb = sent[0][1]["inline_keyboard"]
    assert [b["text"] for b in kb[0]] == ["Draft reply", "Contacted", "Ignore"] and kb[1][0]["url"].startswith("https://u/")
    assert all(len(b["callback_data"].encode()) <= 64 for b in kb[0])


def test_bot_callbacks_and_commands_only_for_my_chat():
    s = Store(":memory:"); s.upsert(_lead("a", 90)); api = Api()
    lead = s.query()[0][0]; pre = lead["id"][:20]
    cb = lambda data, chat=42: {"callback_query": {"id": "9", "data": data, "message": {"chat": {"id": chat}}}}
    assert telegram_bot.handle_update(cb(f"c:{pre}", chat=7), {}, s, api, "42") == "ignored: other chat" and s.get(lead["id"])["status"] == "new"
    assert telegram_bot.handle_update(cb(f"c:{pre}"), {}, s, api, "42") == "contacted"
    got = s.get(lead["id"]); assert got["status"] == "contacted" and got["follow_up"]
    assert telegram_bot.handle_update(cb("d:" + pre), {"profile": {"name": "Sam"}}, s, api, "42") == "drafted"
    assert any(m == "sendMessage" and "Draft (" in p["text"] for m, p in api.calls)
    assert telegram_bot.handle_update(cb("i:" + pre), {}, s, api, "42") == "ignored lead" and s.get(lead["id"])["status"] == "ignored"
    assert telegram_bot.handle_update(cb("c:zzz"), {}, s, api, "42") == "not found"
    s.upsert(_lead("b", 80)); s.upsert(_lead("c", 75))
    msg = lambda t, chat=42: {"message": {"text": t, "chat": {"id": chat}}}
    assert telegram_bot.handle_update(msg("/top"), {}, s, api, "42") == "top 2"
    assert telegram_bot.handle_update(msg("/stats"), {}, s, api, "42") == "stats"
    assert telegram_bot.handle_update(msg("/top", chat=7), {}, s, api, "42") == "ignored: other chat"


def test_digest_content_and_sending(monkeypatch):
    s = Store(":memory:"); s.upsert(_lead("a", 88))
    s.upsert(_lead("b", 60)); s.update_user_fields(s.query(q="number b")[0][0]["id"], status="contacted", follow_up="2000-01-01")
    d = digest.build(s, {})
    assert d["new"] == 1 and d["due"] == 1 and "lead number a" in d["text"] and "Reply in the thread" in d["text"] and "Follow-ups due" in d["text"]
    for v in ("TELEGRAM_BOT_TOKEN", "DISCORD_WEBHOOK_URL", "SMTP_HOST"): monkeypatch.delenv(v, raising=False)
    assert digest.send_digest({}, s)["sent"] is False
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "http://x")
    got = []
    monkeypatch.setattr(notify, "_discord", lambda t: got.append(t))
    assert digest.send_digest({}, s)["sent"] is True and "lead number a" in got[0]
