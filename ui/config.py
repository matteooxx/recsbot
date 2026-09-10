import os


def _int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except ValueError:
        return default


def _bool(key: str, default: bool) -> bool:
    value = os.environ.get(key)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# Auth
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS_HASH = os.environ.get("ADMIN_PASS_HASH", "")
SECRET_KEY = os.environ.get("SECRET_KEY", "")
SESSION_INACTIVITY_HOURS = _int("SESSION_INACTIVITY_HOURS", 3)

# Email (forgot-password)
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = _int("SMTP_PORT", 587)
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "")
OWNER_EMAIL = os.environ.get("OWNER_EMAIL", SMTP_FROM)

# Backend. Local recsbot-backend is the default; a cloud URL remains optional.
RECSBOT_BASE_URL = os.environ.get(
    "RECSBOT_BASE_URL", "http://127.0.0.1:8000"
).rstrip("/")
RECSBOT_BEARER_TOKEN = os.environ.get("RECSBOT_BEARER_TOKEN", "")
RECSBOT_TLS_VERIFY = _bool("RECSBOT_TLS_VERIFY", True)

# Steam
STEAM_API_KEY = os.environ.get("STEAM_API_KEY", "")
STEAM_ID = os.environ.get("STEAM_ID", "")

# Jellyfin
JELLYFIN_BASE_URL = os.environ.get(
    "JELLYFIN_BASE_URL", "http://127.0.0.1:8096"
).rstrip("/")
JELLYFIN_API_KEY = os.environ.get("JELLYFIN_API_KEY", "")
JELLYFIN_USER_ID = os.environ.get("JELLYFIN_USER_ID", "")
