"""Steam Web API client. Read-only: owned games + recent activity.

Requires STEAM_API_KEY (web API key, free at https://steamcommunity.com/dev/apikey)
and STEAM_ID (the 17-digit SteamID64 of the user, e.g. 76561198000000000).
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

import settings

log = logging.getLogger("recsbot-ui.steam")
_BASE = "https://api.steampowered.com"


def _get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    key = settings.get("STEAM_API_KEY")
    sid = settings.get("STEAM_ID")
    if not key or not sid:
        return {}
    params = {"key": key, "steamid": sid, "format": "json", **params}
    r = httpx.get(f"{_BASE}{path}", params=params, timeout=10.0)
    r.raise_for_status()
    return r.json() or {}


def is_configured() -> bool:
    return bool(settings.get("STEAM_API_KEY") and settings.get("STEAM_ID"))


def probe() -> dict[str, Any]:
    """Round-trip ping against the user's profile. Returns {ok, message, sample}."""
    if not is_configured():
        return {"ok": False, "message": "STEAM_API_KEY and STEAM_ID are required"}
    try:
        body = _get(
            "/IPlayerService/GetOwnedGames/v1/",
            {"include_appinfo": 1, "include_played_free_games": 1},
        )
        games = (body.get("response") or {}).get("games") or []
        return {
            "ok": True,
            "message": f"OK — Steam reports {len(games)} games owned",
            "sample": [g.get("name") for g in games[:3]],
        }
    except httpx.HTTPStatusError as e:
        return {"ok": False, "message": f"HTTP {e.response.status_code} from Steam"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


def owned_games(include_appinfo: bool = True) -> list[dict[str, Any]]:
    """All owned games. include_appinfo=True returns name + (sometimes) genres+playtime."""
    body = _get(
        "/IPlayerService/GetOwnedGames/v1/",
        {"include_appinfo": 1 if include_appinfo else 0, "include_played_free_games": 1},
    )
    return (body.get("response") or {}).get("games") or []


def recently_played(count: int = 15) -> list[dict[str, Any]]:
    body = _get("/IPlayerService/GetRecentlyPlayedGames/v1/", {"count": count})
    return (body.get("response") or {}).get("games") or []
