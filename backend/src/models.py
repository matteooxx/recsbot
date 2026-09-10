from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(default="", max_length=8000)
    conversation_id: str | None = None
    taste_profile: dict[str, Any] = Field(default_factory=dict)
    client_request_id: str | None = None
    regenerate: bool = False
