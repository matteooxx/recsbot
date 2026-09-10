"""SQLite persistence adapter implementing the recsbot storage contract."""

from __future__ import annotations

import base64
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from ulid import ULID

import config


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_ulid() -> str:
    return str(ULID())


def _db_path() -> str:
    return config.LOCAL_DB_PATH


def _prepare_parent(path: str) -> None:
    if path == ":memory:":
        return
    Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    path = _db_path()
    _prepare_parent(path)
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    _ensure_schema(conn)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            deleted_at TEXT,
            message_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS conversations_user_updated
            ON conversations(user_id, deleted_at, updated_at DESC);

        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL
                REFERENCES conversations(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            feedback TEXT,
            feedback_at TEXT
        );
        CREATE INDEX IF NOT EXISTS messages_conversation_created
            ON messages(conversation_id, created_at, id);

        CREATE TABLE IF NOT EXISTS preferences (
            user_id TEXT PRIMARY KEY,
            do_not_recommend_json TEXT NOT NULL DEFAULT '[]',
            favorite_genres_json TEXT NOT NULL DEFAULT '[]',
            recent_mood TEXT,
            updated_at TEXT
        );
        """
    )


def _conversation(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "deleted_at": row["deleted_at"],
        "message_count": int(row["message_count"]),
    }


def create_conversation(user_id: str, title: str) -> dict[str, Any]:
    cid = new_ulid()
    now = _now_iso()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO conversations
                (id, user_id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (cid, user_id, title[:120], now, now),
        )
    return {
        "id": cid,
        "title": title[:120],
        "created_at": now,
        "updated_at": now,
        "deleted_at": None,
        "message_count": 0,
    }


def get_conversation_meta(user_id: str, cid: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM conversations WHERE user_id = ? AND id = ?",
            (user_id, cid),
        ).fetchone()
    return _conversation(row) if row else None


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        return max(0, int(json.loads(raw)["offset"]))
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return 0


def _encode_cursor(offset: int) -> str:
    raw = json.dumps({"offset": offset}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode()


def list_conversations(
    user_id: str, *, limit: int = 20, cursor: str | None = None, trash: bool = False
) -> tuple[list[dict[str, Any]], str | None]:
    offset = _decode_cursor(cursor)
    deleted_clause = "deleted_at IS NOT NULL" if trash else "deleted_at IS NULL"
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM conversations
            WHERE user_id = ? AND {deleted_clause}
            ORDER BY updated_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (user_id, limit + 1, offset),
        ).fetchall()
    has_more = len(rows) > limit
    items = [_conversation(row) for row in rows[:limit]]
    return items, _encode_cursor(offset + limit) if has_more else None


def patch_conversation(
    user_id: str,
    cid: str,
    *,
    title: str | None = None,
    deleted: bool | None = None,
) -> dict[str, Any] | None:
    updates: list[str] = ["updated_at = ?"]
    values: list[Any] = [_now_iso()]
    if title is not None:
        updates.append("title = ?")
        values.append(title[:120])
    if deleted is not None:
        updates.append("deleted_at = ?")
        values.append(_now_iso() if deleted else None)
    values.extend([user_id, cid])
    with _connect() as conn:
        cur = conn.execute(
            f"""
            UPDATE conversations SET {", ".join(updates)}
            WHERE user_id = ? AND id = ?
            """,
            values,
        )
        if cur.rowcount == 0:
            return None
        row = conn.execute(
            "SELECT * FROM conversations WHERE user_id = ? AND id = ?",
            (user_id, cid),
        ).fetchone()
    return _conversation(row)


def hard_delete_conversation(user_id: str, cid: str) -> int:
    with _connect() as conn:
        owned = conn.execute(
            "SELECT 1 FROM conversations WHERE user_id = ? AND id = ?",
            (user_id, cid),
        ).fetchone()
        if not owned:
            return 0
        count = int(
            conn.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (cid,)
            ).fetchone()[0]
        )
        conn.execute(
            "DELETE FROM conversations WHERE user_id = ? AND id = ?",
            (user_id, cid),
        )
    return count + 1


def append_message(
    user_id: str,
    cid: str,
    *,
    role: str,
    content: list[dict[str, Any]],
) -> dict[str, Any]:
    mid = new_ulid()
    now = _now_iso()
    with _connect() as conn:
        owned = conn.execute(
            "SELECT 1 FROM conversations WHERE user_id = ? AND id = ?",
            (user_id, cid),
        ).fetchone()
        if not owned:
            raise ValueError("conversation not found")
        conn.execute(
            """
            INSERT INTO messages
                (id, conversation_id, role, content_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (mid, cid, role, json.dumps(content, ensure_ascii=False), now),
        )
        conn.execute(
            """
            UPDATE conversations
            SET updated_at = ?, message_count = message_count + 1
            WHERE id = ?
            """,
            (now, cid),
        )
    return {
        "id": mid,
        "role": role,
        "content": content,
        "created_at": now,
    }


def delete_trailing_assistant_turn(cid: str) -> int:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, role FROM messages
            WHERE conversation_id = ?
            ORDER BY created_at, id
            """,
            (cid,),
        ).fetchall()
        last_user = None
        for index, row in enumerate(rows):
            if row["role"] == "user":
                last_user = index
        if last_user is None:
            return 0
        ids = [row["id"] for row in rows[last_user + 1 :]]
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        conn.execute(f"DELETE FROM messages WHERE id IN ({placeholders})", ids)
        conn.execute(
            """
            UPDATE conversations
            SET message_count = MAX(0, message_count - ?), updated_at = ?
            WHERE id = ?
            """,
            (len(ids), _now_iso(), cid),
        )
    return len(ids)


def set_latest_assistant_feedback(cid: str, value: str) -> bool:
    if value not in {"up", "down", ""}:
        raise ValueError("feedback must be 'up', 'down', or ''")
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id FROM messages
            WHERE conversation_id = ? AND role = 'assistant'
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (cid,),
        ).fetchone()
        if not row:
            return False
        conn.execute(
            "UPDATE messages SET feedback = ?, feedback_at = ? WHERE id = ?",
            (value or None, _now_iso() if value else None, row["id"]),
        )
    return True


def _message(row: sqlite3.Row) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": row["id"],
        "role": row["role"],
        "content": json.loads(row["content_json"]),
        "created_at": row["created_at"],
    }
    if row["feedback"]:
        item["feedback"] = row["feedback"]
    return item


def list_messages(cid: str, *, limit: int | None = None) -> list[dict[str, Any]]:
    with _connect() as conn:
        if limit is None:
            rows = conn.execute(
                """
                SELECT * FROM messages WHERE conversation_id = ?
                ORDER BY created_at, id
                """,
                (cid,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM messages WHERE conversation_id = ?
                ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                (cid, limit),
            ).fetchall()
            rows = list(reversed(rows))
    return [_message(row) for row in rows]


def _load_json_list(raw: str | None) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return sorted(str(item) for item in value if isinstance(item, str))


def get_preferences(user_id: str) -> dict[str, Any]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM preferences WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        return {
            "do_not_recommend": [],
            "favorite_genres": [],
            "recent_mood": None,
            "updated_at": None,
        }
    return {
        "do_not_recommend": _load_json_list(row["do_not_recommend_json"]),
        "favorite_genres": _load_json_list(row["favorite_genres_json"]),
        "recent_mood": row["recent_mood"],
        "updated_at": row["updated_at"],
    }


def _write_preferences(user_id: str, prefs: dict[str, Any]) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO preferences
                (user_id, do_not_recommend_json, favorite_genres_json,
                 recent_mood, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                do_not_recommend_json = excluded.do_not_recommend_json,
                favorite_genres_json = excluded.favorite_genres_json,
                recent_mood = excluded.recent_mood,
                updated_at = excluded.updated_at
            """,
            (
                user_id,
                json.dumps(sorted(set(prefs["do_not_recommend"]))),
                json.dumps(sorted(set(prefs["favorite_genres"]))),
                prefs.get("recent_mood"),
                prefs["updated_at"],
            ),
        )


def update_preferences(
    user_id: str,
    *,
    do_not_recommend: list[str] | None = None,
    favorite_genres: list[str] | None = None,
    recent_mood: str | None = None,
) -> dict[str, Any]:
    prefs = get_preferences(user_id)
    if do_not_recommend is not None:
        prefs["do_not_recommend"] = do_not_recommend
    if favorite_genres is not None:
        prefs["favorite_genres"] = favorite_genres
    if recent_mood is not None:
        prefs["recent_mood"] = recent_mood
    prefs["updated_at"] = _now_iso()
    _write_preferences(user_id, prefs)
    return get_preferences(user_id)


def add_to_string_set(user_id: str, attr: str, value: str) -> dict[str, Any]:
    if attr not in {"do_not_recommend", "favorite_genres"}:
        raise ValueError(f"unknown set attribute: {attr}")
    prefs = get_preferences(user_id)
    prefs[attr] = sorted(set(prefs[attr]) | {value})
    prefs["updated_at"] = _now_iso()
    _write_preferences(user_id, prefs)
    return get_preferences(user_id)


def set_pref_attr(user_id: str, attr: str, value: str) -> dict[str, Any]:
    if attr != "recent_mood":
        raise ValueError(f"unknown scalar attribute: {attr}")
    prefs = get_preferences(user_id)
    prefs[attr] = value
    prefs["updated_at"] = _now_iso()
    _write_preferences(user_id, prefs)
    return get_preferences(user_id)


def messages_to_bedrock(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return model-compatible history while dropping UI-only blocks."""

    model_keys = {
        "text",
        "toolUse",
        "toolResult",
        "image",
        "document",
        "guardContent",
        "reasoningContent",
        "video",
    }

    def strip_ui(content: list[Any]) -> list[Any]:
        return [
            block
            for block in content
            if not isinstance(block, dict) or any(key in model_keys for key in block)
        ]

    out: list[dict[str, Any]] = []
    for message in messages:
        role = "user" if message["role"] == "tool" else message["role"]
        out.append({"role": role, "content": strip_ui(floatify(message["content"]))})
    return out


def floatify(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, list):
        return [floatify(item) for item in value]
    if isinstance(value, dict):
        return {key: floatify(item) for key, item in value.items()}
    if isinstance(value, set):
        return sorted(value)
    return value
