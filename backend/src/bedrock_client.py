"""Bedrock Converse streaming with tool-use loop.

Yields (event_type, payload) tuples. The handler turns those into SSE frames.

Event types yielded:
    text_delta              {"text": "..."}
    tool_use_start          {"id": "...", "name": "...", "input": {...}}
    tool_use_end            {"id": "...", "status": "ok"|"error", "summary": "..."}
    message_stop            {"stop_reason": "end_turn"|"max_tokens"|...}
    usage                   raw usage dict (logged, not surfaced)
    error                   {"code": "...", "message": "..."}

The caller may also receive `_assistant_message` and `_tool_message` payloads — these are
internal "side-channel" events used to surface complete messages so the handler can persist
them to DynamoDB. They are NOT meant to be forwarded as SSE.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import boto3

import config
from tools import registry

_runtime = boto3.client("bedrock-runtime", region_name=config.AWS_REGION)

DEFAULT_SYSTEM = (
    "You are recsbot, a personal taste-recommender for videogames, movies/TV, and MUSIC.\n"
    "Tone: concise, opinionated, friendly. Match the user's language (Italian or English).\n"
    "\n"
    "Data sources:\n"
    "  - Steam: owned games + playtime (videogames).\n"
    "  - Jellyfin: the user's media server. Holds whichever of {Movies, Series, Music}\n"
    "    they actually use — DO NOT assume a category exists. If Jellyfin only has\n"
    "    music, talk about music; if only movies, talk about movies. The taste profile\n"
    "    summary tells you which.\n"
    "\n"
    "Tools you can call:\n"
    "  - get_user_preferences: read the user's persistent prefs (do_not_recommend, "
    "favorite_genres, recent_mood). Call BEFORE recommending so you know what to avoid.\n"
    "  - search_owned_library: search the Steam/Jellyfin libraries the user already has.\n"
    "  - web_search: discover NEW things — news, reviews, recent releases. Don't use "
    "for things the user already owns.\n"
    "  - record_user_preference: save a lasting preference the user expresses.\n"
    "\n"
    "Decision policy:\n"
    "  - 'What should I play/watch/listen to?' with no extra detail → call "
    "get_user_preferences first, then search_owned_library to find candidates from "
    "what they own.\n"
    "  - 'What's new in X?' / 'What should I buy?' → web_search.\n"
    "  - User says 'don't recommend X again' / 'I love Y' → record_user_preference.\n"
    "  - Don't pad with filler. Recommend 2-3 picks max with one-line justifications.\n"
    "\n"
    "Inline citations:\n"
    "  - When you state a fact that came from a web_search result, append the\n"
    "    matching marker like [1] or [2] right after the claim. Numbers refer\n"
    "    to the order of the web_search results in the response, starting at 1.\n"
    "  - Do NOT cite when search_owned_library was the source — those aren't\n"
    "    in the sources panel.\n"
    "  - Don't pile markers like [1][2][3] on a single sentence; one is enough.\n"
    "  - Skip citations entirely if no web_search ran in this turn.\n"
    "\n"
    "Follow-up suggestions:\n"
    "  - As the LAST step of every turn (after your final text), call the\n"
    "    suggest_followups tool with 2-3 prompts the user might tap next.\n"
    "  - Each prompt ≤ 50 characters, in the user's language, framed as the\n"
    "    user's voice (not yours). Examples: 'Più cozy, meno strategia',\n"
    "    'Solo multiplayer', 'Qualcosa di breve'.\n"
    "  - This tool has no side effect — it's purely UX. Don't describe it to\n"
    "    the user; just call it.\n"
)

MAX_TOOL_ROUNDS = 8


@dataclass
class ChatContext:
    """Everything tools may read but the LLM cannot directly control."""

    user_id: str
    taste_profile: dict[str, Any] = field(default_factory=dict)
    tavily_calls: int = 0  # incremented by web_search; capped per-turn


def _summarize_taste_profile(profile: dict[str, Any]) -> str:
    if not profile:
        return "User has not provided a taste profile this session."
    bits: list[str] = []
    steam = profile.get("steam") or {}
    summary = steam.get("owned_summary") or {}
    if summary:
        total = summary.get("total")
        genres = summary.get("by_genre") or {}
        top = ", ".join(f"{k}={v}" for k, v in list(genres.items())[:6])
        bits.append(f"Steam: {total} games owned; genre histogram: {top}")
    recent = steam.get("recent_2_weeks") or []
    if recent:
        names = ", ".join(g.get("name", "?") for g in recent[:5])
        bits.append(f"Steam recent 2w: {names}")
    jelly = profile.get("jellyfin") or {}
    rw = jelly.get("recent_watched") or []
    if rw:

        def _label(it: dict[str, Any]) -> str:
            t = it.get("title", "?")
            artist = it.get("artist")
            return f"{t} ({artist})" if artist else t

        bits.append(f"Jellyfin recent: {', '.join(_label(g) for g in rw[:5])}")
    favs = jelly.get("favorites") or []
    if favs:

        def _label(it: dict[str, Any]) -> str:
            t = it.get("title", "?")
            artist = it.get("artist")
            return f"{t} ({artist})" if artist else t

        bits.append(f"Jellyfin favorites: {', '.join(_label(g) for g in favs[:5])}")
    artists = jelly.get("top_artists") or []
    if artists:
        names = ", ".join(f"{a.get('name', '?')}×{a.get('play_count', 0)}" for a in artists[:8])
        bits.append(f"Jellyfin top artists (by play count): {names}")
    return "\n".join(bits) or "Taste profile present but empty."


def _execute_tool(name: str, args: dict[str, Any], ctx: ChatContext) -> tuple[bool, dict[str, Any]]:
    handler = registry.TOOL_DISPATCH.get(name)
    if handler is None:
        return False, {"error": f"unknown tool {name}"}
    try:
        return True, handler(args, ctx)
    except Exception as e:  # surface the failure to the LLM so it can adapt
        return False, {"error": str(e), "type": type(e).__name__}


async def stream_chat(
    messages: list[dict[str, Any]],
    ctx: ChatContext,
    *,
    system: str | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.7,
) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
    """Run the converse-stream loop, executing tools as Claude requests them.

    `messages` is the running Bedrock-shape message list. The caller has already
    appended the new user turn. We mutate it in place to record assistant + tool
    rounds so the same list can be persisted by the handler.
    """
    if system is None:
        system = DEFAULT_SYSTEM
    # Anchor the model in real time. Without this, web_search calls drift to
    # last year's reviews because the model's training cutoff biases its
    # interpretation of 'recent' / 'this month'. Same date is exposed to the
    # web_search tool so it can pass time_range='week' or 'day' as needed.
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    full_system = (
        f"Today's date is {today} (UTC). When the user asks for 'novità', "
        f"'this month', 'what's new', etc., interpret it relative to this date "
        f"and pass an appropriate freshness window to web_search.\n\n"
        + system
        + "\n\nUser taste profile (auto-summarized):\n"
        + _summarize_taste_profile(ctx.taste_profile)
    )

    for _round_i in range(MAX_TOOL_ROUNDS):
        resp = _runtime.converse_stream(
            modelId=config.BEDROCK_INFERENCE_PROFILE_ID,
            system=[{"text": full_system}],
            messages=messages,
            toolConfig={"tools": registry.TOOL_SPECS},
            inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
        )

        assistant_blocks: list[dict[str, Any]] = []
        text_buf = ""
        cur_tool: dict[str, Any] | None = None
        stop_reason = "end_turn"

        for event in resp["stream"]:
            if "contentBlockStart" in event:
                start = event["contentBlockStart"].get("start") or {}
                if "toolUse" in start:
                    cur_tool = {
                        "toolUseId": start["toolUse"]["toolUseId"],
                        "name": start["toolUse"]["name"],
                        "input_json": "",
                    }
                else:
                    text_buf = ""
            elif "contentBlockDelta" in event:
                delta = event["contentBlockDelta"].get("delta") or {}
                if "text" in delta:
                    text_buf += delta["text"]
                    yield ("text_delta", {"text": delta["text"]})
                elif "toolUse" in delta and cur_tool is not None:
                    cur_tool["input_json"] += delta["toolUse"].get("input", "")
            elif "contentBlockStop" in event:
                if cur_tool is not None:
                    try:
                        parsed_input = json.loads(cur_tool["input_json"] or "{}")
                    except json.JSONDecodeError:
                        parsed_input = {}
                    block = {
                        "toolUse": {
                            "toolUseId": cur_tool["toolUseId"],
                            "name": cur_tool["name"],
                            "input": parsed_input,
                        }
                    }
                    assistant_blocks.append(block)
                    # suggest_followups is a UX-only tool — surface its input
                    # as a structured `followups` event for the UI and skip
                    # the generic tool-pill notification. Falls through to
                    # _execute_tool below so the toolResult round-trip stays
                    # balanced (Bedrock would reject an orphan toolUse).
                    if cur_tool["name"] == "suggest_followups":
                        prompts_raw = parsed_input.get("prompts") or []
                        prompts = [
                            str(p).strip()
                            for p in prompts_raw
                            if isinstance(p, str) and str(p).strip()
                        ][:4]
                        yield ("followups", {"prompts": prompts})
                    else:
                        yield (
                            "tool_use_start",
                            {
                                "id": cur_tool["toolUseId"],
                                "name": cur_tool["name"],
                                "input": parsed_input,
                            },
                        )
                    cur_tool = None
                else:
                    if text_buf:
                        assistant_blocks.append({"text": text_buf})
                        text_buf = ""
            elif "messageStop" in event:
                stop_reason = event["messageStop"].get("stopReason", "end_turn")
            elif "metadata" in event:
                usage = event["metadata"].get("usage")
                if usage:
                    yield ("usage", usage)

        # surface the assembled assistant turn to the handler for persistence
        if assistant_blocks:
            messages.append({"role": "assistant", "content": assistant_blocks})
            yield ("_assistant_message", {"content": assistant_blocks})

        if stop_reason != "tool_use":
            yield ("message_stop", {"stop_reason": stop_reason})
            return

        # execute every tool_use block and append a single tool_result user message
        tool_results: list[dict[str, Any]] = []
        for block in assistant_blocks:
            if "toolUse" not in block:
                continue
            tu = block["toolUse"]
            ok, output = _execute_tool(tu["name"], tu["input"], ctx)
            summary = _summary_for_ui(tu["name"], output, ok)
            # Send BOTH a JSON block (machine-readable, for the model to reason
            # over) AND a text summary (human-readable, more reliable for models
            # that don't always parse JSON cleanly in-context). Doubles the
            # information value at negligible token cost.
            tool_results.append(
                {
                    "toolResult": {
                        "toolUseId": tu["toolUseId"],
                        "content": [
                            {"json": output},
                            {"text": f"Summary: {summary}"},
                        ],
                        "status": "success" if ok else "error",
                    }
                }
            )
            # For web_search, attach a slim sources list so the UI can render
            # an expandable Sources panel under the assistant bubble. Title +
            # URL only — never snippets, to keep the SSE frame small.
            sources_payload: list[dict[str, str]] = []
            if ok and tu["name"] == "web_search":
                for r in (output.get("results") or [])[:5]:
                    if r.get("url"):
                        sources_payload.append(
                            {
                                "title": (r.get("title") or r["url"])[:140],
                                "url": r["url"],
                            }
                        )
            # suggest_followups already produced its structured `followups`
            # event at tool_use_start time; suppress the matching
            # tool_use_end so the UI doesn't see a stray "saved" pill.
            if tu["name"] != "suggest_followups":
                yield (
                    "tool_use_end",
                    {
                        "id": tu["toolUseId"],
                        "status": "ok" if ok else "error",
                        "summary": summary,
                        "sources": sources_payload,
                    },
                )

        if tool_results:
            tool_msg_content = tool_results
            messages.append({"role": "user", "content": tool_msg_content})
            yield ("_tool_message", {"content": tool_msg_content})

    # exceeded MAX_TOOL_ROUNDS
    yield (
        "error",
        {
            "code": "max_tool_rounds",
            "message": f"Tool loop exceeded {MAX_TOOL_ROUNDS} rounds.",
        },
    )


def _summary_for_ui(name: str, output: dict[str, Any], ok: bool) -> str:
    if not ok:
        return f"{name} failed: {output.get('error', 'unknown error')}"
    if name == "search_owned_library":
        n = len(output.get("results") or [])
        return f"{n} match{'es' if n != 1 else ''}"
    if name == "web_search":
        n = len(output.get("results") or [])
        return f"{n} web result{'s' if n != 1 else ''}"
    if name == "get_user_preferences":
        return "loaded"
    if name == "record_user_preference":
        return "saved" if output.get("ok") else "skipped"
    return "ok"
