"""suggest_followups tool — captured by the chat handler, never reaches a
backing service. Calling it from the model is the model's signal that the
turn is wrapping up; the handler converts the input into a structured
`followups` SSE event for the UI.
"""

from __future__ import annotations

from typing import Any

TOOL_SPEC = {
    "toolSpec": {
        "name": "suggest_followups",
        "description": (
            "Call this near the end of every turn (after your final text) to "
            "propose 2-3 short follow-up prompts the user might tap next. "
            "Each prompt is in the user's voice, in their language, ≤50 chars. "
            "Examples: 'Più cozy, meno strategia', 'Solo multiplayer', "
            "'Qualcosa di breve'. Pure UX — no side effect."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "prompts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 2,
                        "maxItems": 4,
                        "description": "2-4 follow-up prompts, in the user's language",
                    }
                },
                "required": ["prompts"],
            }
        },
    }
}


def run(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """No-op handler. The actual UI surfacing happens in the chat handler,
    which inspects tool_use_start events for this tool and emits a
    structured `followups` event. We still need to return something so the
    Bedrock loop can complete the toolResult round-trip cleanly."""
    raw = args.get("prompts") or []
    cleaned = [str(p).strip() for p in raw if isinstance(p, str) and str(p).strip()]
    return {"ok": True, "count": len(cleaned[:4])}
