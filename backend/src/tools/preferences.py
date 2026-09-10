"""get_user_preferences + record_user_preference tools.

The LLM can only call record_user_preference with one of an allow-listed action;
arbitrary attribute writes are blocked even if the LLM tries to inject them.
"""

from __future__ import annotations

from typing import Any

import storage as dynamo

GET_TOOL_SPEC = {
    "toolSpec": {
        "name": "get_user_preferences",
        "description": (
            "Read the user's persistent preferences: do_not_recommend list (titles to "
            "never suggest again), favorite_genres, recent_mood. Call this BEFORE "
            "recommending so you know what to avoid."
        ),
        "inputSchema": {"json": {"type": "object", "properties": {}}},
    }
}

RECORD_TOOL_SPEC = {
    "toolSpec": {
        "name": "record_user_preference",
        "description": (
            "Save a preference the user just expressed. Use sparingly and only when the "
            'user clearly stated a lasting preference ("don\'t recommend X again", '
            '"I love RPGs", "I\'m in a chill mood lately"). Do NOT use for one-off '
            "moods within the current chat — the conversation history covers that."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "add_blocklist",
                            "add_favorite_genre",
                            "set_recent_mood",
                        ],
                        "description": "Which preference field to update",
                    },
                    "value": {
                        "type": "string",
                        "description": "The value to add/set (max 80 chars)",
                    },
                },
                "required": ["action", "value"],
            }
        },
    }
}


def run_get(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return dynamo.get_preferences(ctx.user_id)


def run_record(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    action = args.get("action")
    value = (args.get("value") or "").strip()
    if not value:
        return {"error": "value is required"}
    if len(value) > 80:
        return {"error": "value too long (max 80 chars)"}
    if action == "add_blocklist":
        prefs = dynamo.add_to_string_set(ctx.user_id, "do_not_recommend", value)
    elif action == "add_favorite_genre":
        prefs = dynamo.add_to_string_set(ctx.user_id, "favorite_genres", value)
    elif action == "set_recent_mood":
        prefs = dynamo.set_pref_attr(ctx.user_id, "recent_mood", value)
    else:
        return {"error": f"unknown action: {action}"}
    return {"ok": True, "preferences": prefs}
