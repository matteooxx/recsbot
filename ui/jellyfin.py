"""Jellyfin REST client. Read-only: recent watches + favorites for a single user."""
from __future__ import annotations

import logging
from typing import Any

import httpx

import settings

log = logging.getLogger("recsbot-ui.jellyfin")


def _headers() -> dict[str, str]:
    return {"X-Emby-Token": settings.get("JELLYFIN_API_KEY")}


def _base_url() -> str:
    return (settings.get("JELLYFIN_BASE_URL") or "http://127.0.0.1:8096").rstrip("/")


def _user_url(suffix: str) -> str:
    return f"{_base_url()}/Users/{settings.get('JELLYFIN_USER_ID')}{suffix}"


def is_configured() -> bool:
    return bool(settings.get("JELLYFIN_API_KEY") and settings.get("JELLYFIN_USER_ID"))


def probe() -> dict[str, Any]:
    if not is_configured():
        return {"ok": False, "message": "JELLYFIN_API_KEY and JELLYFIN_USER_ID are required"}
    try:
        r = httpx.get(_user_url(""), headers=_headers(), timeout=8.0)
        r.raise_for_status()
        body = r.json() or {}
        return {
            "ok": True,
            "message": f"OK — connected as {body.get('Name', '?')}",
            "server": body.get("ServerName"),
        }
    except httpx.HTTPStatusError as e:
        return {"ok": False, "message": f"HTTP {e.response.status_code} from Jellyfin"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


def _items_to_simple(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten a Jellyfin Item to a compact dict.

    Mixed-media safe: handles Movie, Series, Episode, MusicAlbum, Audio, MusicArtist.
    `artist` is populated for music items only.
    """
    out = []
    for it in items or []:
        d: dict[str, Any] = {
            "title": it.get("Name"),
            "type": it.get("Type"),
            "genres": it.get("Genres") or [],
        }
        artist = it.get("AlbumArtist") or (it.get("Artists") or [None])[0]
        if artist:
            d["artist"] = artist
        ud = it.get("UserData") or {}
        if ud.get("LastPlayedDate"):
            d["watched_at"] = ud["LastPlayedDate"]
        if ud.get("PlayCount"):
            d["play_count"] = ud["PlayCount"]
        out.append(d)
    return out


# Jellyfin tracks plays at the Audio (track) and Episode level. MusicAlbum/Series rows
# don't carry a play count, so we must query at track granularity for music users.
_PLAYED_TYPES = "Movie,Series,Episode,Audio"
_FAVORITE_TYPES = "Movie,Series,MusicArtist,MusicAlbum,Audio"


def _query_items(params: dict[str, Any]) -> list[dict[str, Any]]:
    r = httpx.get(_user_url("/Items"), params=params, headers=_headers(), timeout=10.0)
    r.raise_for_status()
    return (r.json() or {}).get("Items") or []


def recently_watched(limit: int = 20) -> list[dict[str, Any]]:
    """Recent plays. For music users this returns recent Audio tracks; for movie/series
    users it returns the corresponding items. Mixed libraries get a mix.
    """
    if not is_configured():
        return []
    items = _query_items(
        {
            "Filters": "IsPlayed",
            "SortBy": "DatePlayed",
            "SortOrder": "Descending",
            # Pull more than `limit` because we'll dedupe by (artist, type) on Audio
            # to avoid filling the response with 20 tracks from a single album.
            "Limit": limit * 4,
            "Recursive": "true",
            "IncludeItemTypes": _PLAYED_TYPES,
            "Fields": "Genres,AlbumArtist,Album",
        }
    )
    flat = _items_to_simple(items)
    # Dedupe Audio rows by artist (keep most recent per artist) so the LLM sees variety.
    out: list[dict[str, Any]] = []
    seen_artists: set[str] = set()
    for it in flat:
        if it.get("type") == "Audio":
            artist = (it.get("artist") or "").lower()
            if artist and artist in seen_artists:
                continue
            if artist:
                seen_artists.add(artist)
        out.append(it)
        if len(out) >= limit:
            break
    return out


def favorites(limit: int = 15) -> list[dict[str, Any]]:
    if not is_configured():
        return []
    items = _query_items(
        {
            "Filters": "IsFavorite",
            "Limit": limit,
            "Recursive": "true",
            "IncludeItemTypes": _FAVORITE_TYPES,
            "Fields": "Genres,AlbumArtist",
        }
    )
    return _items_to_simple(items)


def top_artists(limit: int = 10) -> list[dict[str, Any]]:
    """Most-played artists, computed by aggregating played-track counts.

    Jellyfin's MusicArtist rows don't carry an aggregated PlayCount, so we count
    `Audio` items grouped by AlbumArtist. This is the strongest music-taste signal we
    have. Empty list for non-music users.
    """
    if not is_configured():
        return []
    try:
        items = _query_items(
            {
                "Filters": "IsPlayed",
                "Recursive": "true",
                "IncludeItemTypes": "Audio",
                # Big enough to cover a realistic music-listener history.
                "Limit": 2000,
                "Fields": "AlbumArtist,Genres",
            }
        )
    except Exception as e:
        log.warning("top_artists fetch failed: %s", e)
        return []

    # Aggregate UserData.PlayCount per AlbumArtist; fall back to 1 per row if missing.
    counts: dict[str, int] = {}
    genres_by_artist: dict[str, set[str]] = {}
    for it in items:
        artist = it.get("AlbumArtist") or (it.get("Artists") or [None])[0]
        if not artist:
            continue
        pc = int((it.get("UserData") or {}).get("PlayCount") or 0) or 1
        counts[artist] = counts.get(artist, 0) + pc
        for g in it.get("Genres") or []:
            genres_by_artist.setdefault(artist, set()).add(g)

    ranked = sorted(counts.items(), key=lambda kv: -kv[1])[:limit]
    return [
        {"name": name, "play_count": pc, "genres": sorted(genres_by_artist.get(name, []))}
        for name, pc in ranked
    ]
