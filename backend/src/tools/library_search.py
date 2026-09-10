"""search_owned_library tool: filters the user's taste profile in the request scope.

The taste_profile is closed over via ToolContext; the LLM picks query + library, never
controls the data being searched. This is what lets us register the user's whole library
to the model without sending all of it in the system prompt.
"""

from __future__ import annotations

from typing import Any

TOOL_SPEC = {
    "toolSpec": {
        "name": "search_owned_library",
        "description": (
            "Search the user's owned game library (Steam) and/or their Jellyfin media "
            "library (Movies, Series, AND Music — albums and artists). Returns up to 10 "
            "best matches sorted by relevance and play count. Use this when the user "
            "asks for recommendations from things they ALREADY OWN or have played/watched."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search terms — name, genre, or vibe keywords",
                    },
                    "library": {
                        "type": "string",
                        "enum": ["steam", "jellyfin", "both"],
                        "description": "Which library to search",
                    },
                },
                "required": ["query", "library"],
            }
        },
    }
}


def _tokenize(s: str) -> list[str]:
    return [t for t in s.lower().replace(",", " ").split() if t]


def _score_steam(item: dict[str, Any], query_tokens: list[str]) -> int:
    name = (item.get("name") or "").lower()
    genres = [g.lower() for g in item.get("genres") or []]
    score = 0
    for tok in query_tokens:
        if tok in name:
            score += 8
        if any(tok in g for g in genres):
            score += 4
    playtime = float(item.get("playtime_hours") or 0)
    score += min(int(playtime / 5), 10)
    # Recency boost: anything played in the last 2 weeks is much more relevant.
    if (item.get("playtime_hours_2w") or item.get("playtime_2w_hours") or 0) > 0:
        score += 5
    return score


def _score_jellyfin(item: dict[str, Any], query_tokens: list[str]) -> int:
    title = (item.get("title") or item.get("name") or "").lower()
    genres = [g.lower() for g in item.get("genres") or []]
    typ = (item.get("type") or "").lower()
    artist = (item.get("artist") or "").lower()
    score = 0
    for tok in query_tokens:
        if tok in title:
            score += 8
        if artist and tok in artist:
            score += 8
        if any(tok in g for g in genres):
            score += 4
        if tok == typ:
            score += 2
        music_tokens = {"music", "musica", "song", "songs", "album", "artist", "band"}
        if tok in music_tokens and "music" in typ:
            score += 3
        if tok in {"movie", "movies", "film"} and typ == "movie":
            score += 3
        if tok in {"series", "show", "shows", "serie"} and typ in {"series", "episode"}:
            score += 3
    # Tie-breaker: high play count = more relevant signal
    score += min(int((item.get("play_count") or 0) / 5), 5)
    return score


def _filter_steam(steam: dict[str, Any], query: str) -> list[tuple[int, dict[str, Any]]]:
    tokens = _tokenize(query)
    if not tokens:
        return []
    pool: list[dict[str, Any]] = []
    pool.extend(steam.get("recent_2_weeks") or [])
    pool.extend((steam.get("owned_summary") or {}).get("top_played") or [])
    pool.extend(steam.get("all_owned") or [])
    seen: set[Any] = set()
    deduped = []
    for it in pool:
        key = it.get("appid") or it.get("name")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)
    return [
        (
            _score_steam(it, tokens),
            {
                "name": it.get("name"),
                "appid": it.get("appid"),
                "playtime_hours": it.get("playtime_hours"),
                "genres": it.get("genres"),
                "source": "steam",
            },
        )
        for it in deduped
        if _score_steam(it, tokens) > 0
    ]


def _filter_jellyfin(jelly: dict[str, Any], query: str) -> list[tuple[int, dict[str, Any]]]:
    tokens = _tokenize(query)
    if not tokens:
        return []
    pool: list[dict[str, Any]] = []
    pool.extend(jelly.get("recent_watched") or [])
    pool.extend(jelly.get("favorites") or [])
    # top_artists items have shape {name, play_count, genres} — normalize to title shape
    for a in jelly.get("top_artists") or []:
        pool.append(
            {
                "title": a.get("name"),
                "type": "MusicArtist",
                "genres": a.get("genres") or [],
                "play_count": a.get("play_count") or 0,
            }
        )
    seen: set[tuple[str, str]] = set()
    deduped = []
    for it in pool:
        key = (it.get("title") or "", it.get("artist") or "")
        if not key[0] or key in seen:
            continue
        seen.add(key)
        deduped.append(it)
    out: list[tuple[int, dict[str, Any]]] = []
    for it in deduped:
        score = _score_jellyfin(it, tokens)
        if score <= 0:
            continue
        row: dict[str, Any] = {
            "title": it.get("title"),
            "type": it.get("type"),
            "genres": it.get("genres"),
            "source": "jellyfin",
        }
        if it.get("artist"):
            row["artist"] = it["artist"]
        if it.get("watched_at"):
            row["watched_at"] = it["watched_at"]
        if it.get("play_count"):
            row["play_count"] = it["play_count"]
        out.append((score, row))
    return out


def run(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    query = args.get("query") or ""
    library = args.get("library") or "both"
    profile = getattr(ctx, "taste_profile", None) or {}
    scored: list[tuple[int, dict[str, Any]]] = []
    if library in ("steam", "both"):
        scored += _filter_steam(profile.get("steam") or {}, query)
    if library in ("jellyfin", "both"):
        scored += _filter_jellyfin(profile.get("jellyfin") or {}, query)
    # Sort by descending score across both libraries — previously the merged list
    # preserved insertion order, biasing toward whichever library appeared first.
    scored.sort(key=lambda pair: -pair[0])
    items = [it for _, it in scored]
    if not items:
        return {
            "results": [],
            "note": (
                f"no matches in {library} for '{query}'. "
                "User may not own this — consider web_search instead."
            ),
        }
    return {"results": items[:10]}
