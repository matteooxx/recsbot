import os

APP_MODE = os.environ.get("APP_MODE", "local").strip().lower()
STORAGE_BACKEND = (
    os.environ.get("STORAGE_BACKEND", "sqlite" if APP_MODE == "local" else "dynamodb")
    .strip()
    .lower()
)
MODEL_BACKEND = (
    os.environ.get("MODEL_BACKEND", "deterministic" if APP_MODE == "local" else "bedrock")
    .strip()
    .lower()
)
AUTH_BACKEND = (
    os.environ.get("AUTH_BACKEND", "environment" if APP_MODE == "local" else "secrets-manager")
    .strip()
    .lower()
)

# Local mode. The default database path is relative to the process working
# directory; Docker/Compose overrides it with a path in the persistent volume.
LOCAL_DB_PATH = os.environ.get("LOCAL_DB_PATH", "./runtime/recsbot.db")
RECSBOT_AUTH_TOKEN = os.environ.get("RECSBOT_AUTH_TOKEN", "")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")

# Optional AWS deployment mode.
TABLE_NAME = os.environ.get("TABLE_NAME", "recsbot")
BEDROCK_INFERENCE_PROFILE_ID = os.environ.get(
    "BEDROCK_INFERENCE_PROFILE_ID", "eu.anthropic.claude-sonnet-4-6"
)
BEARER_TOKEN_SECRET = os.environ.get("BEARER_TOKEN_SECRET", "recsbot/bearer-token")
TAVILY_SECRET = os.environ.get("TAVILY_SECRET", "recsbot/tavily")
USER_ID = os.environ.get("USER_ID", "default-user")
AWS_REGION = os.environ.get("AWS_REGION", "eu-west-1")
