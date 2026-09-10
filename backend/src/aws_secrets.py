import json
import time

import boto3

import config

_client = None
_cache: dict[str, tuple[float, dict]] = {}
_TTL_SECONDS = 300


def get(name: str) -> dict:
    global _client
    if _client is None:
        _client = boto3.client("secretsmanager", region_name=config.AWS_REGION)
    now = time.monotonic()
    cached = _cache.get(name)
    if cached and now - cached[0] < _TTL_SECONDS:
        return cached[1]
    resp = _client.get_secret_value(SecretId=name)
    raw = resp.get("SecretString") or "{}"
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = {"value": raw}
    _cache[name] = (now, value)
    return value


def invalidate(name: str) -> None:
    """Drop a cached secret so the next get() re-fetches from Secrets Manager.

    Called after auth failure: lets a freshly-rotated bearer token take effect
    immediately on the next request, instead of waiting up to 5 min for TTL.
    """
    _cache.pop(name, None)
