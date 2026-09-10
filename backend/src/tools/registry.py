"""Tool registry. Central place where tool specs (for Bedrock) and handlers
(invoked when Claude requests a tool_use) are listed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tools import followups, library_search, preferences, tavily

TOOL_SPECS: list[dict[str, Any]] = [
    library_search.TOOL_SPEC,
    tavily.TOOL_SPEC,
    preferences.GET_TOOL_SPEC,
    preferences.RECORD_TOOL_SPEC,
    followups.TOOL_SPEC,
]

TOOL_DISPATCH: dict[str, Callable[[dict[str, Any], Any], dict[str, Any]]] = {
    "search_owned_library": library_search.run,
    "web_search": tavily.run,
    "get_user_preferences": preferences.run_get,
    "record_user_preference": preferences.run_record,
    "suggest_followups": followups.run,
}
