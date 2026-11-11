"""Alerts for new hot leads: Telegram, Discord webhook and/or email. Configure via environment (.env).

TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID | DISCORD_WEBHOOK_URL | SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, ALERT_EMAIL_TO
"""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

import requests

from .storage import Store

log = logging.getLogger("leabee.notify")


def channels() -> list[str]:
    e = os.environ.get
    out = []
    if e("TELEGRAM_BOT_TOKEN") and e("TELEGRAM_CHAT_ID"):
        out.append("telegram")
    if e("DISCORD_WEBHOOK_URL"):
        out.append("discord")
    if e("SMTP_HOST") and e("ALERT_EMAIL_TO"):
        out.append("email")
    return out


def format_lead(l: dict) -> str:
    bits = [f"{l.get('grade') or ''} {l['score']} · {l['platform']} · {l.get('country') or 'country unknown'}", l["title"][:200]]
    if l.get("ai_summary"):
        bits.append(l["ai_summary"])
    if l.get("budget_text"):
        bits.append(f"Budget: {l['budget_text']}")
    if l.get("contact"):
        bits.append(f"Contact: {l['contact'][:150]}")
    bits.append(l["url"])
    return "\n".join(bits)


def lead_buttons(l: dict) -> dict:
    """Inline keyboard for a lead alert; leabee.telegram_bot handles the callbacks."""
    i = l["id"][:20]
    return {"inline_keyboard": [[{"text": "Draft reply", "callback_data": f"d:{i}"}, {"text": "Contacted", "callback_data": f"c:{i}"},
                                 {"text": "Ignore", "callback_data": f"i:{i}"}],
                                [{"text": "Open post", "url": l["url"]}]]}


def _telegram(text: str, markup: dict | None = None) -> None:
    e = os.environ
    body = {"chat_id": e["TELEGRAM_CHAT_ID"], "text": text[:4000], "disable_web_page_preview": True}
    if markup:
        body["reply_markup"] = markup
    r = requests.post(f"https://api.telegram.org/bot{e['TELEGRAM_BOT_TOKEN']}/sendMessage", timeout=20, json=body)
    r.raise_for_status()


def _discord(text: str) -> None:
    r = requests.post(os.environ["DISCORD_WEBHOOK_URL"], json={"content": text[:1900]}, timeout=20)
    r.raise_for_status()


def _email(subject: str, text: str) -> None:
    e = os.environ
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, e.get("SMTP_USER") or e["ALERT_EMAIL_TO"], e["ALERT_EMAIL_TO"]
    msg.set_content(text)
    with smtplib.SMTP(e["SMTP_HOST"], int(e.get("SMTP_PORT", 587)), timeout=30) as s:
        s.starttls()
        if e.get("SMTP_USER"):
            s.login(e["SMTP_USER"], e.get("SMTP_PASSWORD", ""))
        s.send_message(msg)


def send(text: str, subject: str = "Leabee alert", skip: tuple = ()) -> list[str]:
    """Send to every configured channel. Returns the channels that succeeded."""
    ok = []
    for ch in channels():
        if ch in skip:
            continue
        try:
            {"telegram": lambda: _telegram(text), "discord": lambda: _discord(text),
             "email": lambda: _email(subject, text)}[ch]()
            ok.append(ch)
        except Exception as ex:
            log.warning("alert via %s failed: %s", ch, ex)
    return ok


def alert_new_hot(cfg: dict, store: Store) -> dict:
    """Notify about unseen leads at or above alert_score. First run only baselines existing leads."""
    ncfg = cfg.get("alerts", {}) or {}
    threshold = int(ncfg.get("min_score", 80))
    if not channels():
        return {"sent": 0, "skipped": "no alert channel configured"}
    if store.get_meta("alerts_baselined") != "1":
        store.baseline_notified(threshold)
        store.set_meta("alerts_baselined", "1")
    leads = store.to_notify(threshold, int(ncfg.get("max_per_run", 10)))
    if not leads:
        return {"sent": 0}
    text = f"{len(leads)} new hot lead(s)" + chr(10)*2 + (chr(10)*2).join(format_lead(l) for l in leads)
    ok = []
    if "telegram" in channels():                      # one message per lead, each with action buttons
        try:
            for l in leads:
                _telegram(format_lead(l), lead_buttons(l))
            ok.append("telegram")
        except Exception as ex:
            log.warning("alert via telegram failed: %s", ex)
    ok += [c for c in send(text, f"Leabee: {len(leads)} new hot lead(s)", skip=("telegram",))]
    if ok:
        store.mark_notified([l["id"] for l in leads])
    return {"sent": len(leads) if ok else 0, "channels": ok}
