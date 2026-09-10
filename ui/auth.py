"""Auth: bcrypt + Flask session, forgot-password email, env-marker write-back.

There is no persistent audit database. After a password change the new bcrypt
hash is written back to the environment file and an .env-changed marker is left
for an optional host-side job that propagates it to the deployment
configuration.
"""
from __future__ import annotations

import logging
import secrets
import threading
from datetime import datetime, timedelta
from functools import wraps

import bcrypt
from flask import Blueprint, jsonify, request, send_from_directory, session

import config
import settings as app_settings
from extensions import limiter
from notifications import send_password_reset_email

# TODO: migrate datetime.utcnow() → datetime.now(UTC) (deprecated in 3.12) when
# we're prepared to invalidate all existing sessions on deploy. The naive vs
# aware datetime comparison in check_auth() would fail mid-deploy otherwise.
log = logging.getLogger("recsbot-ui")
auth_bp = Blueprint("auth", __name__)

current_pass_hash = [config.ADMIN_PASS_HASH]
RESET_TOKEN_TTL_MIN = 30
reset_tokens: dict[str, datetime] = {}
reset_tokens_lock = threading.Lock()


def _persist_admin_hash(new_hash: str) -> bool:
    return app_settings.persist({"ADMIN_PASS_HASH": new_hash})


def _purge_expired_tokens() -> None:
    now = datetime.utcnow()
    with reset_tokens_lock:
        for tok in [t for t, exp in reset_tokens.items() if exp < now]:
            del reset_tokens[tok]


def check_password(password: str, hashed: str) -> bool:
    if not hashed:
        return False
    if hashed.startswith(("$2b$", "$2a$")):
        try:
            return bcrypt.checkpw(password.encode(), hashed.encode())
        except Exception:
            return False
    return False


def check_auth() -> bool:
    if not session.get("authenticated"):
        return False
    last = session.get("last_activity")
    now = datetime.utcnow()
    if last:
        elapsed = (now - datetime.fromisoformat(last)).total_seconds()
        if elapsed > config.SESSION_INACTIVITY_HOURS * 3600:
            session.clear()
            return False
        # Don't rewrite the cookie on every request — only every 60s of activity.
        # Each rewrite is a Set-Cookie header in the response; with the chat UI
        # polling /api/conversations on every send + /api/me at boot it adds up.
        if elapsed < 60:
            return True
    session["last_activity"] = now.isoformat()
    return True


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not check_auth():
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


@auth_bp.route("/api/login", methods=["POST"])
@limiter.limit("10 per minute")
def login():
    data = request.json or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return jsonify({"error": "Credenziali mancanti"}), 400
    if username != config.ADMIN_USER or not check_password(password, current_pass_hash[0]):
        # No artificial delay: flask-limiter's 10/min already throttles brute-force,
        # and a sync time.sleep() on a gthread worker is a self-DoS — 16 wrong
        # attempts pin all workers for ~16s, blocking the chat for legit traffic.
        log.info("login_failed user=%s ip=%s", username, request.remote_addr)
        return jsonify({"error": "Credenziali non valide"}), 401
    session.clear()
    session.permanent = True
    session["authenticated"] = True
    session["username"] = username
    session["last_activity"] = datetime.utcnow().isoformat()
    log.info("login_ok user=%s ip=%s", username, request.remote_addr)
    return jsonify({"ok": True})


@auth_bp.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@auth_bp.route("/api/me")
def me():
    if check_auth():
        return jsonify({"authenticated": True, "username": session.get("username", "")})
    return jsonify({"authenticated": False}), 401


@auth_bp.route("/api/change-password", methods=["POST"])
@require_auth
@limiter.limit("5 per minute")
def change_password():
    data = request.json or {}
    cur = data.get("current") or ""
    new_pass = data.get("new") or ""
    if not cur or not new_pass:
        return jsonify({"error": "Parametri mancanti"}), 400
    if len(new_pass) < 8:
        return jsonify({"error": "La nuova password deve essere di almeno 8 caratteri"}), 400
    if not check_password(cur, current_pass_hash[0]):
        # Same rationale as login: rate limiter handles brute-force; sync sleep
        # is a self-DoS on gthread workers.
        return jsonify({"error": "Password attuale non corretta"}), 401
    new_hash = bcrypt.hashpw(new_pass.encode(), bcrypt.gensalt()).decode()
    current_pass_hash[0] = new_hash
    persisted = _persist_admin_hash(new_hash)
    return jsonify({"ok": True, "persisted": persisted})


@auth_bp.route("/api/forgot-password", methods=["POST"])
@limiter.limit("3 per hour")
def forgot_password():
    ip = request.remote_addr or ""
    _purge_expired_tokens()
    if not config.OWNER_EMAIL or not config.SMTP_USER or not config.SMTP_PASS:
        return jsonify({"ok": True})  # mute the actual reason — don't reveal config
    token = secrets.token_urlsafe(32)
    expires = datetime.utcnow() + timedelta(minutes=RESET_TOKEN_TTL_MIN)
    with reset_tokens_lock:
        reset_tokens[token] = expires
    base = request.host_url.rstrip("/")
    reset_link = f"{base}/reset-password?token={token}"
    threading.Thread(
        target=send_password_reset_email,
        args=(config.OWNER_EMAIL, reset_link, RESET_TOKEN_TTL_MIN, ip),
        daemon=True,
    ).start()
    return jsonify({"ok": True})


@auth_bp.route("/reset-password")
def reset_password_page():
    return send_from_directory("templates", "reset.html")


@auth_bp.route("/api/reset-password", methods=["POST"])
@limiter.limit("10 per hour")
def reset_password():
    data = request.json or {}
    token = data.get("token") or ""
    new_pass = data.get("new") or ""
    if not token or not new_pass:
        return jsonify({"error": "Parametri mancanti"}), 400
    if len(new_pass) < 8:
        return jsonify({"error": "La nuova password deve essere di almeno 8 caratteri"}), 400
    _purge_expired_tokens()
    with reset_tokens_lock:
        expiry = reset_tokens.pop(token, None)
    if not expiry or expiry < datetime.utcnow():
        return jsonify({"error": "Token non valido o scaduto"}), 401
    new_hash = bcrypt.hashpw(new_pass.encode(), bcrypt.gensalt()).decode()
    current_pass_hash[0] = new_hash
    persisted = _persist_admin_hash(new_hash)
    return jsonify({"ok": True, "persisted": persisted})
