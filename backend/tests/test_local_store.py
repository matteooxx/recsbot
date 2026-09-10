from __future__ import annotations

import asyncio
from pathlib import Path

import config
import local_model
import local_store


def _use_database(tmp_path: Path) -> None:
    config.LOCAL_DB_PATH = str(tmp_path / "recsbot.db")


def test_conversation_lifecycle(tmp_path: Path) -> None:
    _use_database(tmp_path)
    meta = local_store.create_conversation("user", "First chat")
    cid = meta["id"]

    local_store.append_message("user", cid, role="user", content=[{"text": "Recommend a game"}])
    local_store.append_message("user", cid, role="assistant", content=[{"text": "Try Portal 2"}])

    conversations, cursor = local_store.list_conversations("user")
    assert cursor is None
    assert conversations[0]["message_count"] == 2
    assert [row["role"] for row in local_store.list_messages(cid)] == [
        "user",
        "assistant",
    ]

    assert local_store.set_latest_assistant_feedback(cid, "up")
    assert local_store.list_messages(cid)[-1]["feedback"] == "up"
    assert local_store.delete_trailing_assistant_turn(cid) == 1

    trashed = local_store.patch_conversation("user", cid, deleted=True)
    assert trashed and trashed["deleted_at"]
    assert local_store.list_conversations("user")[0] == []
    assert len(local_store.list_conversations("user", trash=True)[0]) == 1
    assert local_store.hard_delete_conversation("user", cid) == 2


def test_preferences_round_trip(tmp_path: Path) -> None:
    _use_database(tmp_path)
    local_store.add_to_string_set("user", "favorite_genres", "RPG")
    local_store.add_to_string_set("user", "do_not_recommend", "Example")
    local_store.set_pref_attr("user", "recent_mood", "relaxed")

    prefs = local_store.get_preferences("user")
    assert prefs["favorite_genres"] == ["RPG"]
    assert prefs["do_not_recommend"] == ["Example"]
    assert prefs["recent_mood"] == "relaxed"


def test_deterministic_model_uses_local_library(tmp_path: Path) -> None:
    _use_database(tmp_path)
    config.MODEL_BACKEND = "deterministic"
    messages = [{"role": "user", "content": [{"text": "What should I play?"}]}]
    ctx = local_model.ChatContext(
        user_id="user",
        taste_profile={
            "steam": {
                "recent_2_weeks": [{"name": "Portal 2"}],
                "owned_summary": {},
                "all_owned": [],
            }
        },
    )

    async def collect() -> list[tuple[str, dict]]:
        return [event async for event in local_model.stream_chat(messages, ctx)]

    events = asyncio.run(collect())
    text = next(payload["text"] for kind, payload in events if kind == "text_delta")
    assert "Portal 2" in text
    assert events[-1][0] == "message_stop"
