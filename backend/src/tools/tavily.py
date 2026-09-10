"""web_search tool — Tavily REST. Per-turn call cap is enforced via ctx.tavily_calls."""

from __future__ import annotations

import logging
from typing import Any

import httpx

import aws_secrets
import config

log = logging.getLogger("recsbot.tavily")

PER_TURN_CAP = 4

TOOL_SPEC = {
    "toolSpec": {
        "name": "web_search",
        "description": (
            "Search the web for things the user does NOT already own — new releases, "
            "reviews, current events, what's trending. Use for discovery questions and "
            "anything time-sensitive. Do NOT use for items in the user's own library; "
            "use search_owned_library for those."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Search query — be specific, include the current year "
                            "(provided in the system prompt) when asking for new "
                            "releases or 'what's out now'."
                        ),
                    },
                    "freshness": {
                        "type": "string",
                        "enum": ["day", "week", "month", "year", "any"],
                        "description": (
                            "How recent results must be. Default 'month' for news/"
                            "discovery; pick 'day' or 'week' when the user explicitly "
                            "asks for today/this week; 'any' only for evergreen lookups "
                            "like 'best RPGs of all time'."
                        ),
                    },
                    "topic": {
                        "type": "string",
                        "enum": ["news", "general"],
                        "description": (
                            "'news' biases toward dated articles (best for releases, "
                            "reviews, announcements). 'general' is broader (forums, "
                            "wikis, evergreen pages). Default 'news'."
                        ),
                    },
                },
                "required": ["query"],
            }
        },
    }
}


def _api_key() -> str:
    payload = aws_secrets.get(config.TAVILY_SECRET)
    key = payload.get("api_key") or payload.get("value")
    if not key:
        raise RuntimeError(f"secret {config.TAVILY_SECRET} missing 'api_key' or 'value' field")
    return key


def run(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    calls = getattr(ctx, "tavily_calls", 0)
    if calls >= PER_TURN_CAP:
        return {
            "error": f"web_search call cap reached ({PER_TURN_CAP}/turn). "
            "Wrap up with what you have or ask the user to clarify.",
        }
    ctx.tavily_calls = calls + 1

    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}

    # Default to a tight, news-biased lookup. Tavily's defaults (general topic,
    # all-time) drag in evergreen SEO pages and the model ends up summarizing
    # last year's content as 'now'. The model can override per call.
    freshness = (args.get("freshness") or "month").lower()
    topic = (args.get("topic") or "news").lower()
    payload: dict[str, Any] = {
        "api_key": _api_key(),
        "query": query,
        "max_results": 5,
        "search_depth": "basic",
        "include_answer": True,
        "topic": "news" if topic == "news" else "general",
    }
    if freshness in ("day", "week", "month", "year"):
        payload["time_range"] = freshness
    # 'any' → omit time_range entirely

    try:
        r = httpx.post("https://api.tavily.com/search", json=payload, timeout=15.0)
        r.raise_for_status()
    except httpx.HTTPError as e:
        log.warning("tavily call failed: %s", e)
        return {"error": f"tavily HTTP error: {e}"}

    body = r.json() or {}
    return {
        "answer": body.get("answer"),
        "freshness": freshness,
        "topic": payload["topic"],
        "results": [
            {
                "title": x.get("title"),
                "url": x.get("url"),
                "snippet": (x.get("content") or "")[:400],
                "published_date": x.get("published_date"),
            }
            for x in (body.get("results") or [])[:5]
        ],
    }
