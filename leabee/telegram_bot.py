"""Telegram bot: act on leads from your phone. Run with `python -m leabee bot` (long polling, needs no public URL).

Buttons on every alert: Draft reply / Contacted (sets a follow-up in 3 days) / Ignore.   Commands: /top  /stats  /due
Only the chat in TELEGRAM_CHAT_ID is answered; every other chat is ignored."""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta

import requests

from .notify import format_lead, lead_buttons
from .outreach import draft
from .storage import Store

log = logging.getLogger("leabee.bot")


class TelegramApi:
    def __init__(self, token: str):
        self.base = f"https://api.telegram.org/bot{token}"

    def call(self, method: str, **params) -> dict:
        r = requests.post(f"{self.base}/{method}", json=params, timeout=int(params.get("timeout", 0)) + 20)
        r.raise_for_status()
        return r.json()


def handle_update(upd: dict, cfg: dict, store: Store, api, chat_id: str) -> str:
    """Process one Telegram update; returns what was done (for logs/tests)."""
    cb = upd.get("callback_query")
    if cb:
        if str(cb.get("message", {}).get("chat", {}).get("id")) != str(chat_id):
            return "ignored: other chat"
        action, _, prefix = (cb.get("data") or "").partition(":")
        lead = store.find_by_prefix(prefix)
        if not lead:
            api.call("answerCallbackQuery", callback_query_id=cb["id"], text="Lead not found")
            return "not found"
        if action == "c":
            store.update_user_fields(lead["id"], status="contacted", follow_up=(datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d"))
            api.call("answerCallbackQuery", callback_query_id=cb["id"], text="Marked contacted, follow-up in 3 days")
            return "contacted"
        if action == "i":
            store.update_user_fields(lead["id"], status="ignored")
            api.call("answerCallbackQuery", callback_query_id=cb["id"], text="Ignored")
            return "ignored lead"
        if action == "d":
            api.call("answerCallbackQuery", callback_query_id=cb["id"], text="Writing draft...")
            d = draft(lead, cfg)
            api.call("sendMessage", chat_id=chat_id, text=f"Draft ({d['source']}), edit before sending:\n\n{d['short']}", disable_web_page_preview=True)
            return "drafted"
        return "unknown action"
    msg = upd.get("message") or {}
    if str(msg.get("chat", {}).get("id")) != str(chat_id):
        return "ignored: other chat"
    cmd = (msg.get("text") or "").split()[0].split("@")[0].lower() if msg.get("text") else ""
    if cmd == "/top":
        rows, _ = store.query(status="new", min_score=50, limit=5)
        for l in rows:
            api.call("sendMessage", chat_id=chat_id, text=format_lead(l), reply_markup=lead_buttons(l), disable_web_page_preview=True)
        if not rows:
            api.call("sendMessage", chat_id=chat_id, text="No new leads at grade C or better.")
        return f"top {len(rows)}"
    if cmd == "/stats":
        st = store.stats()
        api.call("sendMessage", chat_id=chat_id, text=f"{st['total']} active leads, {st['hot']} hot, {st['with_contact']} with contact, {st['follow_ups_due']} follow-ups due")
        return "stats"
    if cmd == "/due":
        rows, _ = store.query(follow_due=True, limit=10, sort="follow_up")
        api.call("sendMessage", chat_id=chat_id, text="Follow-ups due:\n" + ("\n".join(f"- {l['title'][:70]} ({l['follow_up']})" for l in rows) or "none"))
        return "due"
    if cmd in ("/start", "/help"):
        api.call("sendMessage", chat_id=chat_id, text="Commands: /top (best new leads), /stats, /due (follow-ups). Use the buttons on alerts to draft, mark contacted or ignore.")
        return "help"
    return "ignored"


def run(cfg: dict, store: Store, once: bool = False) -> None:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env first.")
    api, offset = TelegramApi(token), 0
    while True:
        try:
            for upd in api.call("getUpdates", offset=offset, timeout=0 if once else 50, allowed_updates=["message", "callback_query"]).get("result", []):
                offset = upd["update_id"] + 1
                log.info("update -> %s", handle_update(upd, cfg, store, api, chat))
        except requests.RequestException as e:
            log.warning("telegram poll failed: %s", e)
            time.sleep(5)
        if once:
            return
