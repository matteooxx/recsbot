import json
from typing import Any


def sse(event_type: str, **fields: Any) -> str:
    payload = {"type": event_type, **fields}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def heartbeat() -> str:
    return ": ping\n\n"
