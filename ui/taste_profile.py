"""Build the compact taste-profile JSON sent to the Lambda on every chat request.

Strategy:
- owned_summary keeps a genre histogram + top 10 by playtime — small + always sent.
- recent_2_weeks: at most 15 entries.
- jellyfin recent_watched (max 20) + favorites (max 10).
- all_owned: full library (~25 KB at the user's current scale). Skip if it ever
  becomes too big and rely on the LLM calling search_owned_library against the summary.

Per-source TTLs reflect how often each source actually changes:

  Steam owned games (catalog):       1 hour    — buying a new game is rare
  Steam recent_2_weeks:              10 min    — playtime updates frequently
  Jellyfin recent_watched:           2 min     — track-by-track plays
  Jellyfin favorites:                15 min    — manually starred, rare changes
  Jellyfin top_artists:              30 min    — heavy aggregation, low churn
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

import jellyfin
import steam

log = logging.getLogger("recsbot-ui.taste")

_STEAM_OWNED_TTL = 3600
_STEAM_RECENT_TTL = 600
_JELLY_RECENT_TTL = 120
_JELLY_FAV_TTL = 900
_JELLY_TOP_TTL = 1800

_cache: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()


def _cached(key: str, ttl: int, factory: Callable[[], Any], force: bool) -> Any:
    """Tiny per-key TTL cache."""
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
    if hit and not force and now - hit[0] < ttl:
        return hit[1]
    value = factory()
    with _lock:
        _cache[key] = (now, value)
    return value


def _from_steam_game(g: dict[str, Any]) -> dict[str, Any]:
    return {
        "appid": g.get("appid"),
        "name": g.get("name"),
        "playtime_hours": round((g.get("playtime_forever") or 0) / 60, 1),
        "playtime_2w_hours": round((g.get("playtime_2weeks") or 0) / 60, 1),
        "genres": [],
    }


def _summarize_steam(owned: list[dict[str, Any]]) -> dict[str, Any]:
    by_genre: dict[str, int] = {}
    for g in owned:
        for genre in g.get("genres") or []:
            by_genre[genre] = by_genre.get(genre, 0) + 1
    sorted_by_play = sorted(owned, key=lambda x: -(x.get("playtime_hours") or 0))
    top = sorted_by_play[:10]
    return {
        "total": len(owned),
        "by_genre": dict(sorted(by_genre.items(), key=lambda x: -x[1])),
        "top_played": top,
    }


def _build_steam_owned(force: bool) -> dict[str, Any]:
    def factory() -> dict[str, Any]:
        try:
            owned_raw = steam.owned_games()
            owned = [_from_steam_game(g) for g in owned_raw]
            return {
                "owned_summary": _summarize_steam(owned),
                "all_owned": owned,
            }
        except Exception as e:
            log.warning("steam owned fetch failed: %s", e)
            return {"owned_summary": {"total": 0, "by_genre": {}, "top_played": []}, "all_owned": []}
    return _cached("steam_owned", _STEAM_OWNED_TTL, factory, force)


def _build_steam_recent(force: bool) -> list[dict[str, Any]]:
    def factory() -> list[dict[str, Any]]:
        try:
            recent_raw = steam.recently_played()
            return [
                {**_from_steam_game(g), "playtime_hours_2w": round((g.get("playtime_2weeks") or 0) / 60, 1)}
                for g in recent_raw
            ][:15]
        except Exception as e:
            log.warning("steam recent fetch failed: %s", e)
            return []
    return _cached("steam_recent", _STEAM_RECENT_TTL, factory, force)


def _build_jelly_recent(force: bool) -> list[dict[str, Any]]:
    def factory() -> list[dict[str, Any]]:
        try:
            return jellyfin.recently_watched(20)
        except Exception as e:
            log.warning("jellyfin recent fetch failed: %s", e)
            return []
    return _cached("jelly_recent", _JELLY_RECENT_TTL, factory, force)


def _build_jelly_favs(force: bool) -> list[dict[str, Any]]:
    def factory() -> list[dict[str, Any]]:
        try:
            return jellyfin.favorites(15)
        except Exception as e:
            log.warning("jellyfin favorites fetch failed: %s", e)
            return []
    return _cached("jelly_favs", _JELLY_FAV_TTL, factory, force)


def _build_jelly_top(force: bool) -> list[dict[str, Any]]:
    def factory() -> list[dict[str, Any]]:
        try:
            return jellyfin.top_artists(10)
        except Exception as e:
            log.warning("jellyfin top_artists fetch failed: %s", e)
            return []
    return _cached("jelly_top", _JELLY_TOP_TTL, factory, force)


def build(force: bool = False) -> dict[str, Any]:
    """Return the current taste profile, with per-source TTL caching.

    Cold cache: ~1.5s (Steam + Jellyfin in parallel via threadpool would be ~600ms,
    but at single-user scale the serial version is fine).
    Warm: instant.
    """
    steam_owned = _build_steam_owned(force)
    steam_recent = _build_steam_recent(force)
    return {
        "steam": {
            "owned_summary": steam_owned["owned_summary"],
            "recent_2_weeks": steam_recent,
            "all_owned": steam_owned["all_owned"],
        },
        "jellyfin": {
            "recent_watched": _build_jelly_recent(force),
            "favorites": _build_jelly_favs(force),
            "top_artists": _build_jelly_top(force),
        },
    }
