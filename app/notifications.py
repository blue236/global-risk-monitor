from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Optional, Tuple, List, Dict, Any

import httpx


def _telegram_config() -> Tuple[str, str]:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    return token, chat_id


def send_telegram(message: str) -> Optional[str]:
    token, chat_id = _telegram_config()
    if not token or not chat_id:
        return "TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not configured"

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.post(url, data={"chat_id": chat_id, "text": message})
            if r.status_code >= 400:
                return f"Telegram error {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return f"Telegram exception: {e}"
    return None


def fetch_telegram_updates(offset: int | None = None, timeout: int = 0) -> tuple[list[dict], Optional[str]]:
    token, _chat_id = _telegram_config()
    if not token:
        return [], "TELEGRAM_BOT_TOKEN not configured"

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    payload: Dict[str, Any] = {"timeout": max(0, int(timeout))}
    if offset is not None:
        payload["offset"] = int(offset)
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.post(url, data=payload)
            if r.status_code >= 400:
                return [], f"Telegram getUpdates error {r.status_code}: {r.text[:200]}"
            j = r.json()
            return (j.get("result") or []), None
    except Exception as e:
        return [], f"Telegram getUpdates exception: {e}"


def telegram_chat_id() -> str:
    _token, chat_id = _telegram_config()
    return chat_id


def send_email(subject: str, body: str) -> Optional[str]:
    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT", "587").strip() or "587")
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip()
    mail_from = os.getenv("SMTP_FROM", user).strip()
    mail_to = os.getenv("SMTP_TO", "").strip()

    if not host or not mail_to:
        return "SMTP_HOST/SMTP_TO not configured"

    recipients = [x.strip() for x in mail_to.split(",") if x.strip()]
    if not recipients:
        return "SMTP_TO empty"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = mail_from
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)

    context = ssl.create_default_context()
    try:
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            if user and password:
                server.login(user, password)
            server.send_message(msg)
    except Exception as e:
        return f"Email exception: {e}"
    return None
