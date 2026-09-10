"""recsbot-ui: authenticated chat UI and SSE proxy."""
from __future__ import annotations

import json
import logging
import sys
import time
from typing import Iterator

import httpx
from flask import Flask, Response, jsonify, redirect, request, send_from_directory, stream_with_context

import config
import jellyfin
import settings as app_settings
import steam
import taste_profile
from auth import auth_bp, require_auth
from extensions import limiter

# JSON structured logging, one object per line on stdout
class _JSONFormatter(logging.Formatter):
    def format(self, record):
        out = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False)


_root = logging.getLogger()
_root.setLevel(logging.INFO)
for h in _root.handlers[:]:
    _root.removeHandler(h)
_h = logging.StreamHandler(sys.stdout)
_h.setFormatter(_JSONFormatter())
_root.addHandler(_h)
log = logging.getLogger("recsbot-ui")


app = Flask(__name__, static_folder="static", template_folder="templates")
if not config.SECRET_KEY:
    raise RuntimeError("SECRET_KEY must be set")
app.config.update(
    SECRET_KEY=config.SECRET_KEY,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=24 * 3600,
    JSON_AS_ASCII=False,
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,  # 2 MB — chat bodies are small
)
limiter.init_app(app)
app.register_blueprint(auth_bp)


@app.before_request
def _csrf_check():
    """CSRF guard: every non-GET /api/* call must come from same-origin XHR."""
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return None
    if not request.path.startswith("/api/"):
        return None
    if request.path in {"/api/login", "/api/forgot-password", "/api/reset-password"}:
        return None
    if request.headers.get("X-Requested-With") != "XMLHttpRequest":
        return jsonify({"error": "CSRF: missing X-Requested-With"}), 403
    return None


# ------------------- pages -------------------

@app.route("/")
def index():
    from auth import check_auth
    if not check_auth():
        return redirect("/login")
    return send_from_directory("templates", "index.html")


@app.route("/login")
def login_page():
    from auth import check_auth
    if check_auth():
        return redirect("/")
    return send_from_directory("templates", "login.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/favicon.svg")
def favicon():
    return Response(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<rect width="32" height="32" rx="8" fill="#6c63ff"/>'
        '<text x="16" y="22" text-anchor="middle" font-size="20" fill="white">🎯</text>'
        "</svg>",
        mimetype="image/svg+xml",
    )


# ------------------- backend proxy -------------------

def _backend(path: str) -> str:
    base = app_settings.get("RECSBOT_BASE_URL") or config.RECSBOT_BASE_URL
    if not base:
        raise RuntimeError("RECSBOT_BASE_URL is empty")
    return f"{base.rstrip('/')}{path}"


def _signed_headers(method: str, url: str, body: bytes | str | None = None) -> dict[str, str]:
    """Build application headers for local or remotely hosted backends."""
    bearer = config.RECSBOT_BEARER_TOKEN or app_settings.get("RECSBOT_BEARER_TOKEN")
    headers: dict[str, str] = {}
    if bearer:
        headers["X-Recsbot-Token"] = bearer
    if body is not None:
        headers["Content-Type"] = "application/json"
    return headers


_HTTPX_VERIFY = config.RECSBOT_TLS_VERIFY


def _signed_get(path: str, params: dict | None = None, timeout: float = 10.0) -> httpx.Response:
    url = _backend(path)
    if params:
        req = httpx.Request("GET", url, params=params)
        url = str(req.url)
    headers = _signed_headers("GET", url)
    return httpx.get(url, headers=headers, timeout=timeout, verify=_HTTPX_VERIFY)


def _signed_send(method: str, path: str, *, json_body: dict | None = None, params: dict | None = None, timeout: float = 10.0) -> httpx.Response:
    url = _backend(path)
    if params:
        req = httpx.Request(method, url, params=params)
        url = str(req.url)
    body_bytes = None
    if json_body is not None:
        body_bytes = json.dumps(json_body).encode()
    headers = _signed_headers(method, url, body_bytes)
    return httpx.request(method, url, headers=headers, content=body_bytes, timeout=timeout, verify=_HTTPX_VERIFY)


@app.route("/api/chat", methods=["POST"])
@require_auth
def chat_proxy() -> Response:
    body = request.get_json(silent=True) or {}
    payload = {
        "message": (body.get("message") or "").strip(),
        "conversation_id": body.get("conversation_id"),
        "taste_profile": taste_profile.build(),
        "client_request_id": body.get("client_request_id"),
        "regenerate": bool(body.get("regenerate")),
    }
    if not payload["regenerate"] and not payload["message"]:
        return jsonify({"error": "message required"}), 400

    url = _backend("/chat")
    body_bytes = json.dumps(payload).encode()
    headers = _signed_headers("POST", url, body_bytes)

    def gen() -> Iterator[bytes]:
        with httpx.stream(
            "POST",
            url,
            content=body_bytes,
            headers=headers,
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0),
            verify=_HTTPX_VERIFY,
        ) as r:
            if r.status_code != 200:
                err = r.read().decode("utf-8", "replace")[:200]
                # Use json.dumps so embedded quotes/newlines don't break the SSE frame.
                payload = json.dumps({
                    "type": "error",
                    "code": f"backend_{r.status_code}",
                    "message": err,
                })
                yield f"data: {payload}\n\n".encode()
                return
            for raw in r.iter_raw():
                if raw:
                    yield raw

    return Response(
        stream_with_context(gen()),
        mimetype="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@app.route("/api/conversations", methods=["GET"])
@require_auth
def list_conversations():
    r = _signed_get("/conversations", params=dict(request.args))
    return Response(r.text, status=r.status_code, mimetype="application/json")


@app.route("/api/conversations/<cid>", methods=["GET"])
@require_auth
def get_conversation(cid: str):
    r = _signed_get(f"/conversations/{cid}")
    return Response(r.text, status=r.status_code, mimetype="application/json")


@app.route("/api/conversations/<cid>", methods=["PATCH"])
@require_auth
def patch_conversation(cid: str):
    r = _signed_send("PATCH", f"/conversations/{cid}", json_body=request.get_json(silent=True) or {})
    return Response(r.text, status=r.status_code, mimetype="application/json")


@app.route("/api/conversations/<cid>", methods=["DELETE"])
@require_auth
def delete_conversation(cid: str):
    r = _signed_send("DELETE", f"/conversations/{cid}", params=dict(request.args))
    return Response(r.text, status=r.status_code, mimetype="application/json")


@app.route("/api/conversations/<cid>/feedback", methods=["POST"])
@require_auth
def post_feedback(cid: str):
    r = _signed_send("POST", f"/conversations/{cid}/feedback", json_body=request.get_json(silent=True) or {})
    return Response(r.text, status=r.status_code, mimetype="application/json")


@app.route("/api/preferences", methods=["GET"])
@require_auth
def get_prefs():
    r = _signed_get("/preferences")
    return Response(r.text, status=r.status_code, mimetype="application/json")


@app.route("/api/preferences", methods=["PATCH"])
@require_auth
def patch_prefs():
    r = _signed_send("PATCH", "/preferences", json_body=request.get_json(silent=True) or {})
    return Response(r.text, status=r.status_code, mimetype="application/json")


@app.route("/api/taste-profile", methods=["GET"])
@require_auth
def taste_dump():
    """Debug endpoint: dump the current taste profile the frontend sends to Lambda."""
    return jsonify(taste_profile.build(force=request.args.get("refresh") == "1"))


# ------------------- settings (UI-managed creds) -------------------

@app.route("/api/settings", methods=["GET"])
@require_auth
def get_settings():
    return jsonify(app_settings.public_view())


@app.route("/api/settings", methods=["PATCH"])
@require_auth
def patch_settings():
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"error": "body must be an object"}), 400
    to_save: dict[str, str] = {}
    for k in app_settings.MANAGED_KEYS:
        if k not in body:
            continue
        v = body[k]
        # If a secret field comes back as empty string, treat that as 'leave alone'.
        # Front-end sends "" for unchanged secrets and the actual value for changes.
        if k in app_settings.SECRET_KEYS and v == "":
            continue
        if not isinstance(v, str):
            return jsonify({"error": f"{k} must be a string"}), 400
        if len(v) > 4096:
            return jsonify({"error": f"{k} too long"}), 400
        to_save[k] = v.strip()
    if not to_save:
        return jsonify(app_settings.public_view())
    app_settings.set_many(to_save)
    persisted = app_settings.persist(to_save)
    # bust the taste-profile cache so the next chat picks up the new creds
    taste_profile.build(force=True)
    return jsonify({"ok": True, "persisted": persisted, "settings": app_settings.public_view()})


@app.route("/api/settings/test/steam", methods=["POST"])
@require_auth
def test_steam():
    return jsonify(steam.probe())


@app.route("/api/settings/test/jellyfin", methods=["POST"])
@require_auth
def test_jellyfin():
    return jsonify(jellyfin.probe())


@app.route("/api/healthz/backend", methods=["GET"])
@require_auth
def backend_healthz():
    try:
        r = _signed_get("/health", timeout=5.0)
        return jsonify({"backend_status": r.status_code, "body": r.json()})
    except Exception as e:
        return jsonify({"error": str(e)}), 502
