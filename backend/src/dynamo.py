"""Single-table DynamoDB access layer for recsbot.

Schema:
    pk = USER#<userId>      sk = CONV#<ulid>     (conversation meta)
    pk = CONV#<ulid>        sk = MSG#<ulid>      (messages)
    pk = USER#<userId>      sk = PREF            (preferences)

GSI gsi1 on (gsi1pk, gsi1sk) for "list user's conversations sorted by updated_at":
    gsi1pk = USER#<userId>#LIVE  or  USER#<userId>#TRASH
    gsi1sk = updated_at (ISO8601)
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.exceptions import ClientError
from ulid import ULID

import config

_ddb = boto3.resource("dynamodb", region_name=config.AWS_REGION)
_table = _ddb.Table(config.TABLE_NAME)
# Low-level client for TransactWriteItems (the resource client doesn't expose it
# through .Table directly).
_client = boto3.client("dynamodb", region_name=config.AWS_REGION)
_serializer = None  # lazily initialized to avoid module-import-time work


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_ulid() -> str:
    return str(ULID())


def _user_pk(user_id: str) -> str:
    return f"USER#{user_id}"


def _conv_pk(cid: str) -> str:
    return f"CONV#{cid}"


def _gsi1pk_live(user_id: str) -> str:
    return f"USER#{user_id}#LIVE"


def _gsi1pk_trash(user_id: str) -> str:
    return f"USER#{user_id}#TRASH"


# ------------------- conversations -------------------


def create_conversation(user_id: str, title: str) -> dict[str, Any]:
    cid = new_ulid()
    now = _now_iso()
    item = {
        "pk": _user_pk(user_id),
        "sk": f"CONV#{cid}",
        "id": cid,
        "title": title[:120],
        "created_at": now,
        "updated_at": now,
        "deleted_at": None,
        "message_count": 0,
        "gsi1pk": _gsi1pk_live(user_id),
        "gsi1sk": now,
    }
    _table.put_item(Item=item)
    return item


def get_conversation_meta(user_id: str, cid: str) -> dict[str, Any] | None:
    resp = _table.get_item(Key={"pk": _user_pk(user_id), "sk": f"CONV#{cid}"})
    return resp.get("Item")


def list_conversations(
    user_id: str, *, limit: int = 20, cursor: str | None = None, trash: bool = False
) -> tuple[list[dict[str, Any]], str | None]:
    gsi1pk = _gsi1pk_trash(user_id) if trash else _gsi1pk_live(user_id)
    kwargs: dict[str, Any] = {
        "IndexName": "gsi1",
        "KeyConditionExpression": "gsi1pk = :pk",
        "ExpressionAttributeValues": {":pk": gsi1pk},
        "ScanIndexForward": False,
        "Limit": limit,
    }
    if cursor:
        kwargs["ExclusiveStartKey"] = json.loads(base64.urlsafe_b64decode(cursor).decode())
    resp = _table.query(**kwargs)
    items = [
        {
            "id": x["id"],
            "title": x.get("title"),
            "created_at": x.get("created_at"),
            "updated_at": x.get("updated_at"),
            "deleted_at": x.get("deleted_at"),
            "message_count": int(x.get("message_count", 0)),
        }
        for x in resp.get("Items", [])
    ]
    next_cursor = None
    lek = resp.get("LastEvaluatedKey")
    if lek:
        next_cursor = base64.urlsafe_b64encode(json.dumps(lek, default=str).encode()).decode()
    return items, next_cursor


def patch_conversation(
    user_id: str,
    cid: str,
    *,
    title: str | None = None,
    deleted: bool | None = None,
) -> dict[str, Any] | None:
    sets: list[str] = []
    values: dict[str, Any] = {}
    names: dict[str, str] = {}
    now = _now_iso()
    if title is not None:
        sets.append("#t = :t")
        names["#t"] = "title"
        values[":t"] = title[:120]
    if deleted is True:
        sets.append("deleted_at = :da")
        sets.append("gsi1pk = :gp")
        values[":da"] = now
        values[":gp"] = _gsi1pk_trash(user_id)
    elif deleted is False:
        sets.append("deleted_at = :da")
        sets.append("gsi1pk = :gp")
        values[":da"] = None
        values[":gp"] = _gsi1pk_live(user_id)
    if not sets:
        return get_conversation_meta(user_id, cid)
    sets.append("updated_at = :u")
    sets.append("gsi1sk = :u")
    values[":u"] = now
    expr = "SET " + ", ".join(sets)
    update_kwargs: dict[str, Any] = {
        "Key": {"pk": _user_pk(user_id), "sk": f"CONV#{cid}"},
        "UpdateExpression": expr,
        "ExpressionAttributeValues": values,
        "ConditionExpression": "attribute_exists(pk)",
        "ReturnValues": "ALL_NEW",
    }
    if names:
        update_kwargs["ExpressionAttributeNames"] = names
    try:
        resp = _table.update_item(**update_kwargs)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return None
        raise
    return resp.get("Attributes")


def hard_delete_conversation(user_id: str, cid: str) -> int:
    """Delete the conversation meta + every message under it. Returns deleted count."""
    keys: list[dict[str, str]] = [{"pk": _user_pk(user_id), "sk": f"CONV#{cid}"}]
    last: dict | None = None
    while True:
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": "pk = :p",
            "ExpressionAttributeValues": {":p": _conv_pk(cid)},
            "ProjectionExpression": "pk, sk",
        }
        if last:
            kwargs["ExclusiveStartKey"] = last
        r = _table.query(**kwargs)
        keys.extend({"pk": x["pk"], "sk": x["sk"]} for x in r.get("Items", []))
        last = r.get("LastEvaluatedKey")
        if not last:
            break
    with _table.batch_writer() as bw:
        for k in keys:
            bw.delete_item(Key=k)
    return len(keys)


# ------------------- messages -------------------


def append_message(
    user_id: str,
    cid: str,
    *,
    role: str,
    content: list[dict[str, Any]],
) -> dict[str, Any]:
    """Atomically write a new message item AND bump the conversation meta.

    Implementation note: the original two-step put_item + update_item could leave
    a phantom message if the Lambda died between calls. TransactWriteItems
    guarantees both succeed or neither does. Same DynamoDB cost (2 WCU per call,
    same as the prior pair).
    """
    global _serializer
    if _serializer is None:
        from boto3.dynamodb.types import TypeSerializer

        _serializer = TypeSerializer()

    mid = new_ulid()
    now = _now_iso()
    item = {
        "pk": _conv_pk(cid),
        "sk": f"MSG#{mid}",
        "id": mid,
        "role": role,
        "content": _decimalize(content),
        "created_at": now,
    }
    serialized_item = {k: _serializer.serialize(v) for k, v in item.items()}

    _client.transact_write_items(
        TransactItems=[
            {"Put": {"TableName": config.TABLE_NAME, "Item": serialized_item}},
            {
                "Update": {
                    "TableName": config.TABLE_NAME,
                    "Key": {
                        "pk": {"S": _user_pk(user_id)},
                        "sk": {"S": f"CONV#{cid}"},
                    },
                    "UpdateExpression": "SET updated_at = :u, gsi1sk = :u ADD message_count :one",
                    "ExpressionAttributeValues": {
                        ":u": {"S": now},
                        ":one": {"N": "1"},
                    },
                },
            },
        ]
    )
    return item


def delete_trailing_assistant_turn(cid: str) -> int:
    """Remove the most-recent assistant turn (and any tool turns that come after the
    last user turn). Returns count deleted. Used by /chat regenerate.
    """
    msgs = list_messages(cid)
    # Find the index of the last user message; everything after it is fair game.
    last_user_idx = None
    for i, m in enumerate(msgs):
        if m["role"] == "user":
            last_user_idx = i
    if last_user_idx is None:
        return 0
    to_delete = msgs[last_user_idx + 1 :]
    if not to_delete:
        return 0
    with _table.batch_writer() as bw:
        for m in to_delete:
            bw.delete_item(Key={"pk": _conv_pk(cid), "sk": f"MSG#{m['id']}"})
    return len(to_delete)


def set_latest_assistant_feedback(cid: str, value: str) -> bool:
    """Stamp the most-recent assistant message in `cid` with `feedback=value`.
    Returns True if a message was updated, False if there is no assistant
    message yet. Idempotent — overwrites any prior feedback for the same turn.
    """
    if value not in ("up", "down", ""):
        raise ValueError("feedback must be 'up', 'down', or '' (clear)")
    # Walk backwards a small window — we only need the latest assistant turn.
    msgs = list_messages(cid, limit=8)
    target = None
    for m in reversed(msgs):
        if m.get("role") == "assistant":
            target = m
            break
    if not target:
        return False
    if value:
        _table.update_item(
            Key={"pk": _conv_pk(cid), "sk": f"MSG#{target['id']}"},
            UpdateExpression="SET feedback = :v, feedback_at = :t",
            ExpressionAttributeValues={":v": value, ":t": _now_iso()},
        )
    else:
        _table.update_item(
            Key={"pk": _conv_pk(cid), "sk": f"MSG#{target['id']}"},
            UpdateExpression="REMOVE feedback, feedback_at",
        )
    return True


def list_messages(cid: str, *, limit: int | None = None) -> list[dict[str, Any]]:
    """Return messages in chronological order.

    When `limit` is set we query *backwards* (ScanIndexForward=False) and stop
    after `limit` items, then reverse to chronological order. This avoids
    loading a large conversation entirely just to slice off the tail —
    materially cheaper at scale (memory + Dynamo RCU).

    When `limit` is None we still need the whole thing (used by
    `get_conversation` which renders all turns).
    """
    items: list[dict[str, Any]] = []
    last: dict | None = None
    forward = limit is None
    while True:
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": "pk = :p AND begins_with(sk, :m)",
            "ExpressionAttributeValues": {":p": _conv_pk(cid), ":m": "MSG#"},
            "ScanIndexForward": forward,
        }
        if limit is not None:
            kwargs["Limit"] = limit - len(items)
        if last:
            kwargs["ExclusiveStartKey"] = last
        r = _table.query(**kwargs)
        items.extend(r.get("Items", []))
        last = r.get("LastEvaluatedKey")
        if not last or (limit is not None and len(items) >= limit):
            break
    if not forward:
        items.reverse()
    return items


# ------------------- preferences -------------------


def get_preferences(user_id: str) -> dict[str, Any]:
    resp = _table.get_item(Key={"pk": _user_pk(user_id), "sk": "PREF"})
    item = resp.get("Item") or {}
    return {
        "do_not_recommend": sorted(item.get("do_not_recommend") or []),
        "favorite_genres": sorted(item.get("favorite_genres") or []),
        "recent_mood": item.get("recent_mood"),
        "updated_at": item.get("updated_at"),
    }


def update_preferences(
    user_id: str,
    *,
    do_not_recommend: list[str] | None = None,
    favorite_genres: list[str] | None = None,
    recent_mood: str | None = None,
) -> dict[str, Any]:
    sets: list[str] = ["updated_at = :u"]
    values: dict[str, Any] = {":u": _now_iso()}
    if do_not_recommend is not None:
        sets.append("do_not_recommend = :dnr")
        values[":dnr"] = set(do_not_recommend) if do_not_recommend else None
    if favorite_genres is not None:
        sets.append("favorite_genres = :fg")
        values[":fg"] = set(favorite_genres) if favorite_genres else None
    if recent_mood is not None:
        sets.append("recent_mood = :rm")
        values[":rm"] = recent_mood
    _table.update_item(
        Key={"pk": _user_pk(user_id), "sk": "PREF"},
        UpdateExpression="SET " + ", ".join(sets),
        ExpressionAttributeValues=values,
    )
    return get_preferences(user_id)


def add_to_string_set(user_id: str, attr: str, value: str) -> dict[str, Any]:
    """Add a value to a StringSet pref attribute. Used by record_user_preference tool."""
    if attr not in {"do_not_recommend", "favorite_genres"}:
        raise ValueError(f"unknown set attribute: {attr}")
    _table.update_item(
        Key={"pk": _user_pk(user_id), "sk": "PREF"},
        UpdateExpression=f"ADD {attr} :v SET updated_at = :u",
        ExpressionAttributeValues={":v": {value}, ":u": _now_iso()},
    )
    return get_preferences(user_id)


def set_pref_attr(user_id: str, attr: str, value: str) -> dict[str, Any]:
    if attr not in {"recent_mood"}:
        raise ValueError(f"unknown scalar attribute: {attr}")
    _table.update_item(
        Key={"pk": _user_pk(user_id), "sk": "PREF"},
        UpdateExpression=f"SET {attr} = :v, updated_at = :u",
        ExpressionAttributeValues={":v": value, ":u": _now_iso()},
    )
    return get_preferences(user_id)


# ------------------- helpers -------------------


def messages_to_bedrock(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert stored message items (role + content list) to Bedrock Converse messages.

    Stored content is already in Bedrock's content-block shape: each block is one of
    {"text": "..."} | {"toolUse": {...}} | {"toolResult": {...}}.
    Our 'tool' role maps back to Bedrock's 'user' role with toolResult blocks.

    Defensively repairs orphan tool_use blocks: if an assistant turn declares a
    tool_use that the next user turn doesn't answer with a matching tool_result,
    splice in a synthetic error tool_result. Without this, ANY conversation that
    lost a tool_result write (e.g. a DynamoDB float-rejection mid-write) becomes
    permanently un-loadable because Bedrock rejects the unbalanced history.
    """
    # Bedrock's Converse API rejects unknown content-block types. We persist
    # extra UI-only blocks (e.g. {"sources":[...]} produced by the chat
    # handler from web_search) on assistant messages; filter those out before
    # we ship history back to the model. Whitelist matches the Converse spec.
    _BEDROCK_BLOCK_KEYS = {
        "text",
        "toolUse",
        "toolResult",
        "image",
        "document",
        "guardContent",
        "reasoningContent",
        "video",
    }

    def _strip_ui_blocks(content: list[Any]) -> list[Any]:
        if not isinstance(content, list):
            return content
        return [
            b
            for b in content
            if not isinstance(b, dict) or any(k in _BEDROCK_BLOCK_KEYS for k in b)
        ]

    out: list[dict[str, Any]] = []
    for m in messages:
        role = "user" if m["role"] == "tool" else m["role"]
        out.append({"role": role, "content": _strip_ui_blocks(floatify(m["content"]))})

    i = 0
    while i < len(out):
        msg = out[i]
        if msg["role"] == "assistant":
            tu_ids = [
                b["toolUse"]["toolUseId"]
                for b in msg["content"] or []
                if isinstance(b, dict) and "toolUse" in b
            ]
            if tu_ids:
                next_result_ids: list[str] = []
                if i + 1 < len(out) and out[i + 1]["role"] == "user":
                    next_result_ids = [
                        b["toolResult"]["toolUseId"]
                        for b in out[i + 1]["content"] or []
                        if isinstance(b, dict) and "toolResult" in b
                    ]
                missing = [tid for tid in tu_ids if tid not in next_result_ids]
                if missing:
                    synthetic = {
                        "role": "user",
                        "content": [
                            {
                                "toolResult": {
                                    "toolUseId": tid,
                                    "content": [
                                        {"text": "tool result unavailable (history repair)"}
                                    ],
                                    "status": "error",
                                }
                            }
                            for tid in missing
                        ],
                    }
                    out.insert(i + 1, synthetic)
        i += 1
    return out


def _decimalize(d: Any) -> Any:
    """Recursively convert floats to Decimal so DynamoDB's resource client accepts them.

    DynamoDB rejects native Python floats with 'Float types are not supported. Use
    Decimal types instead.' Strings, ints, bools, None pass through unchanged.
    """
    from decimal import Decimal

    if isinstance(d, float):
        return Decimal(str(d))
    if isinstance(d, list):
        return [_decimalize(x) for x in d]
    if isinstance(d, dict):
        return {k: _decimalize(v) for k, v in d.items()}
    return d


def floatify(d: Any) -> Any:
    """DynamoDB resource client returns Decimals; flatten to JSON-friendly types."""
    from decimal import Decimal

    if isinstance(d, Decimal):
        return int(d) if d == d.to_integral_value() else float(d)
    if isinstance(d, list):
        return [floatify(x) for x in d]
    if isinstance(d, dict):
        return {k: floatify(v) for k, v in d.items()}
    if isinstance(d, set):
        return sorted(d)
    return d
