"""Email notifications. Stripped to just the password-reset flow recsbot-ui needs."""
from __future__ import annotations

import logging
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape as _html_escape

import config

log = logging.getLogger("recsbot-ui")


def _safe_subject(text: str) -> str:
    return text.replace("\r", "").replace("\n", "")


def send_password_reset_email(
    recipient: str, reset_link: str, ttl_minutes: int, requester_ip: str = ""
) -> None:
    if not (config.SMTP_USER and config.SMTP_PASS and config.SMTP_FROM and recipient):
        log.error("password reset email skipped: SMTP not configured")
        return
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = _safe_subject("recsbot — Reset password")
        msg["From"] = config.SMTP_FROM
        msg["To"] = recipient
        safe_link = _html_escape(reset_link)
        safe_ip = _html_escape(requester_ip or "?")
        when = datetime.utcnow().strftime("%d/%m/%Y %H:%M")
        html = (
            '<html><body style="font-family:sans-serif;background:#0a0a0f;color:#e8e8f0;padding:2rem;">'
            '<div style="max-width:500px;margin:0 auto;background:#111118;border:1px solid #2a2a3a;border-radius:16px;padding:2rem;">'
            '<h2 style="color:#6c63ff;margin-bottom:1rem;">recsbot — Reset password</h2>'
            f'<p>Qualcuno (IP <strong>{safe_ip}</strong>) ha richiesto un reset della password admin alle {when} UTC.</p>'
            f'<p style="margin:1.5rem 0;"><a href="{safe_link}" style="display:inline-block;padding:0.75rem 1.5rem;background:#6c63ff;color:white;border-radius:8px;text-decoration:none;font-weight:bold;">Reimposta la password</a></p>'
            f'<p style="color:#6b6b80;font-size:0.85rem;">Il link scade tra <strong>{ttl_minutes} minuti</strong> ed è utilizzabile una sola volta.</p>'
            '<p style="color:#6b6b80;font-size:0.85rem;">Se non sei stato tu, ignora questa email — la password attuale resta valida.</p>'
            f'<p style="color:#6b6b80;font-size:0.75rem;margin-top:1.5rem;word-break:break-all;">URL: {safe_link}</p>'
            '</div></body></html>'
        )
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as server:
            server.starttls()
            server.login(config.SMTP_USER, config.SMTP_PASS)
            server.sendmail(config.SMTP_FROM, recipient, msg.as_string())
        log.info("password reset email sent to %s (ip=%s)", recipient, requester_ip)
    except Exception as e:
        log.error("password reset email failed: %s", e)
