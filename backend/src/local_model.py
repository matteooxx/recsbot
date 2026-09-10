"""Local chat adapters: deterministic offline mode and optional Ollama."""

from __future__ import annotations

import re
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

import httpx

import config
import storage


@dataclass
class ChatContext:
    user_id: str
    taste_profile: dict[str, Any] = field(default_factory=dict)
    tavily_calls: int = 0


def _text_from_content(content: list[dict[str, Any]]) -> str:
    return " ".join(
        str(block.get("text", "")).strip()
        for block in content
        if isinstance(block, dict) and block.get("text")
    ).strip()


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            text = _text_from_content(message.get("content") or [])
            if text:
                return text
    return ""


def _is_italian(text: str) -> bool:
    lowered = f" {text.lower()} "
    markers = (
        " cosa ",
        " consigli",
        " gioco ",
        " film ",
        " musica ",
        " voglio ",
        " non ",
        " qualcosa ",
        " grazie ",
    )
    return any(marker in lowered for marker in markers)


def _candidate_rows(profile: dict[str, Any], query: str) -> list[dict[str, str]]:
    query_lower = query.lower()
    wants_games = any(word in query_lower for word in ("game", "play", "gioc"))
    wants_music = any(word in query_lower for word in ("music", "song", "album", "artist"))
    wants_video = any(
        word in query_lower for word in ("movie", "film", "watch", "show", "series", "tv")
    )
    rows: list[dict[str, str]] = []
    steam = profile.get("steam") or {}
    jellyfin = profile.get("jellyfin") or {}

    if wants_games or not (wants_music or wants_video):
        game_pool: list[dict[str, Any]] = []
        game_pool.extend(steam.get("recent_2_weeks") or [])
        game_pool.extend((steam.get("owned_summary") or {}).get("top_played") or [])
        game_pool.extend(steam.get("all_owned") or [])
        for item in game_pool:
            name = str(item.get("name") or "").strip()
            if name:
                rows.append({"name": name, "source": "Steam library"})

    if wants_music or wants_video or not wants_games:
        media_pool: list[dict[str, Any]] = []
        media_pool.extend(jellyfin.get("favorites") or [])
        media_pool.extend(jellyfin.get("recent_watched") or [])
        for item in media_pool:
            item_type = str(item.get("type") or "").lower()
            is_music = any(word in item_type for word in ("audio", "music", "album", "artist"))
            if wants_music and not is_music:
                continue
            if wants_video and is_music:
                continue
            name = str(item.get("title") or item.get("name") or "").strip()
            artist = str(item.get("artist") or "").strip()
            if name:
                label = f"{name} by {artist}" if artist else name
                rows.append({"name": label, "source": "Jellyfin library"})

    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for row in rows:
        key = row["name"].casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def _record_explicit_preference(text: str, user_id: str) -> str | None:
    patterns = (
        r"(?:do not|don't|never)\s+recommend\s+(.+?)[.!]?$",
        r"non\s+consigliarmi\s+(?:pi[uù]\s+)?(.+?)[.!]?$",
    )
    for pattern in patterns:
        match = re.search(pattern, text.strip(), flags=re.IGNORECASE)
        if match:
            value = match.group(1).strip(" \"'")
            if value:
                storage.add_to_string_set(user_id, "do_not_recommend", value[:80])
                return value[:80]
    return None


def _deterministic_reply(text: str, ctx: ChatContext) -> tuple[str, list[str]]:
    italian = _is_italian(text)
    blocked_now = _record_explicit_preference(text, ctx.user_id)
    if blocked_now:
        if italian:
            return (
                f"Ricevuto: non consiglierò più {blocked_now}.",
                ["Mostrami le preferenze", "Consigliami qualcos'altro"],
            )
        return (
            f"Saved: I will not recommend {blocked_now} again.",
            ["Show my preferences", "Recommend something else"],
        )

    prefs = storage.get_preferences(ctx.user_id)
    blocked = [value.casefold() for value in prefs["do_not_recommend"]]
    candidates = [
        row
        for row in _candidate_rows(ctx.taste_profile, text)
        if not any(value in row["name"].casefold() for value in blocked)
    ][:3]

    asks_for_pick = any(
        token in text.lower()
        for token in (
            "recommend",
            "suggest",
            "what should",
            "consigl",
            "cosa guard",
            "cosa gioc",
            "cosa ascolt",
        )
    )
    if candidates and asks_for_pick:
        lines = [
            f"{index}. {row['name']} - already present in your {row['source']}."
            for index, row in enumerate(candidates, start=1)
        ]
        if italian:
            lines = [
                f"{index}. {row['name']} - è già nella tua libreria {row['source']}."
                for index, row in enumerate(candidates, start=1)
            ]
            intro = "Scelte locali basate sulla tua libreria:"
            followups = ["Qualcosa di più breve", "Solo giochi", "Solo film o serie"]
        else:
            intro = "Local picks based on your library:"
            followups = ["Something shorter", "Games only", "Movies or series only"]
        return "\n".join([intro, *lines]), followups

    if asks_for_pick:
        if italian:
            return (
                "Non ho ancora elementi locali sufficienti. Configura Steam o Jellyfin "
                "nel frontend, oppure indicami alcuni titoli e generi che ti piacciono.",
                ["Imposta un genere preferito", "Consigliami un gioco", "Consigliami un film"],
            )
        return (
            "I do not have enough local library data yet. Configure Steam or Jellyfin "
            "in the frontend, or tell me a few titles and genres you like.",
            ["Set a favorite genre", "Recommend a game", "Recommend a movie"],
        )

    if italian:
        return (
            "La modalità locale è attiva. Posso scegliere dalla libreria Steam/Jellyfin "
            "fornita dal frontend e ricordare preferenze e titoli da evitare.",
            ["Cosa dovrei giocare?", "Cosa dovrei guardare?", "Mostrami le preferenze"],
        )
    return (
        "Local mode is active. I can choose from the Steam/Jellyfin library supplied "
        "by the frontend and remember preferences or titles to avoid.",
        ["What should I play?", "What should I watch?", "Show my preferences"],
    )


async def _ollama_reply(messages: list[dict[str, Any]], ctx: ChatContext) -> str:
    prompt_messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "You are a concise personal taste recommender. Never claim that an "
                "item is owned unless it appears in the supplied local taste profile. "
                "Recommend at most three items."
            ),
        }
    ]
    profile_summary = str(ctx.taste_profile)[:12000]
    prompt_messages.append(
        {"role": "system", "content": f"Local taste profile:\n{profile_summary}"}
    )
    for message in messages[-20:]:
        role = message.get("role")
        if role not in {"user", "assistant"}:
            continue
        text = _text_from_content(message.get("content") or [])
        if text:
            prompt_messages.append({"role": role, "content": text})

    async with httpx.AsyncClient(timeout=90.0) as client:
        response = await client.post(
            f"{config.OLLAMA_BASE_URL}/api/chat",
            json={
                "model": config.OLLAMA_MODEL,
                "messages": prompt_messages,
                "stream": False,
            },
        )
        response.raise_for_status()
        body = response.json()
    text = str((body.get("message") or {}).get("content") or "").strip()
    if not text:
        raise RuntimeError("Ollama returned an empty response")
    return text


async def stream_chat(
    messages: list[dict[str, Any]],
    ctx: ChatContext,
    *,
    system: str | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.7,
) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
    del system, max_tokens, temperature
    user_text = _last_user_text(messages)

    followups: list[str]
    if config.MODEL_BACKEND == "ollama":
        try:
            reply = await _ollama_reply(messages, ctx)
            followups = (
                ["Go narrower", "Use my library", "Show another option"]
                if not _is_italian(user_text)
                else ["Più specifico", "Usa la mia libreria", "Un'altra opzione"]
            )
        except (httpx.HTTPError, RuntimeError):
            reply, followups = _deterministic_reply(user_text, ctx)
    else:
        reply, followups = _deterministic_reply(user_text, ctx)

    yield ("text_delta", {"text": reply})
    yield ("followups", {"prompts": followups[:3]})
    content = [{"text": reply}]
    yield ("_assistant_message", {"content": content})
    yield ("message_stop", {"stop_reason": "end_turn"})
