import logging
import uuid

from fastapi import Body, Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

import chat_client as bedrock_client
import config
import storage as dynamo
from auth import require_bearer
from models import ChatRequest
from streaming import sse

logging.basicConfig(level=logging.INFO, format='{"level":"%(levelname)s","msg":"%(message)s"}')
log = logging.getLogger("recsbot")

HISTORY_LIMIT = 40  # 20 turns × 2 (user + assistant)

app = FastAPI(title="recsbot", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ------------------- chat -------------------


@app.post("/chat", dependencies=[Depends(require_bearer)])
async def chat(req: ChatRequest, request: Request) -> StreamingResponse:
    # Per-request correlation id. Logged on every event the handler emits and
    # surfaced to the client so they can quote it in support. Defends against
    # leaking AWS internals via raw exception strings (audit #1).
    request_id = uuid.uuid4().hex[:12]

    user_id = config.USER_ID
    cid = req.conversation_id
    new_conversation = False
    title: str | None = None
    if req.regenerate:
        if not cid:
            raise HTTPException(status_code=400, detail="regenerate requires conversation_id")
        meta = dynamo.get_conversation_meta(user_id, cid)
        if not meta:
            raise HTTPException(status_code=404, detail="conversation not found")
        title = meta.get("title")
        dynamo.delete_trailing_assistant_turn(cid)
    elif cid:
        meta = dynamo.get_conversation_meta(user_id, cid)
        if not meta:
            raise HTTPException(status_code=404, detail="conversation not found")
        title = meta.get("title")
    else:
        if not req.message.strip():
            raise HTTPException(status_code=400, detail="message required for new conversation")
        title = req.message[:60].strip() or "New conversation"
        meta = dynamo.create_conversation(user_id, title)
        cid = meta["id"]
        new_conversation = True

    history = dynamo.list_messages(cid, limit=HISTORY_LIMIT)
    bedrock_history = dynamo.messages_to_bedrock(history)

    if not req.regenerate:
        if not req.message.strip():
            raise HTTPException(status_code=400, detail="message required")
        user_content = [{"text": req.message}]
        dynamo.append_message(user_id, cid, role="user", content=user_content)
        bedrock_history.append({"role": "user", "content": user_content})

    ctx = bedrock_client.ChatContext(user_id=user_id, taste_profile=req.taste_profile or {})

    # Sources + follow-up prompts collected during the current assistant
    # turn. Both are appended to the assistant message at persistence time
    # as UI-only content blocks so they survive page reload.
    pending_sources: list[dict[str, str]] = []
    pending_followups: list[str] = []

    async def gen():
        if new_conversation:
            yield sse("conversation_created", conversation_id=cid, title=title)
        try:
            async for event_type, payload in bedrock_client.stream_chat(bedrock_history, ctx):
                if event_type == "_assistant_message":
                    content = list(payload["content"])
                    if pending_sources:
                        content.append({"sources": pending_sources.copy()})
                        pending_sources.clear()
                    if pending_followups:
                        content.append({"followups": pending_followups.copy()})
                        pending_followups.clear()
                    dynamo.append_message(user_id, cid, role="assistant", content=content)
                    continue
                if event_type == "_tool_message":
                    dynamo.append_message(user_id, cid, role="tool", content=payload["content"])
                    continue
                if event_type == "usage":
                    log.info("bedrock_usage cid=%s rid=%s u=%s", cid, request_id, payload)
                    continue
                if event_type == "tool_use_end" and payload.get("sources"):
                    pending_sources.extend(payload["sources"])
                if event_type == "followups" and payload.get("prompts"):
                    # Slice-assign so we mutate the outer list rather than
                    # rebinding the closure name (would shadow + break
                    # _assistant_message branch).
                    pending_followups[:] = payload["prompts"]
                yield sse(event_type, **payload)
        except Exception:
            # Log the full exception server-side; expose only a generic message + the
            # request id to the client. Stops table names / ARNs / region info from
            # leaking to the browser.
            log.exception("chat stream failed cid=%s rid=%s", cid, request_id)
            yield sse(
                "error",
                code="internal_error",
                message=(
                    f"Errore interno (rif. {request_id}). Riprova; se persiste, condividi l'id."
                ),
            )

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
            "X-Request-Id": request_id,
        },
    )


# ------------------- conversations -------------------


@app.get("/conversations", dependencies=[Depends(require_bearer)])
async def list_conversations(
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = None,
    trash: bool = False,
):
    items, next_cursor = dynamo.list_conversations(
        config.USER_ID, limit=limit, cursor=cursor, trash=trash
    )
    return JSONResponse({"items": dynamo.floatify(items), "next_cursor": next_cursor})


@app.get("/conversations/{cid}", dependencies=[Depends(require_bearer)])
async def get_conversation(cid: str = Path(..., min_length=20, max_length=40)):
    meta = dynamo.get_conversation_meta(config.USER_ID, cid)
    if not meta:
        raise HTTPException(status_code=404, detail="not found")
    msgs = dynamo.list_messages(cid)
    body = {
        "id": meta["id"],
        "title": meta.get("title"),
        "created_at": meta.get("created_at"),
        "updated_at": meta.get("updated_at"),
        "deleted_at": meta.get("deleted_at"),
        "messages": [
            {
                "id": m["id"],
                "role": m["role"],
                "content": m["content"],
                "created_at": m["created_at"],
                **({"feedback": m["feedback"]} if m.get("feedback") else {}),
            }
            for m in msgs
        ],
    }
    return JSONResponse(dynamo.floatify(body))


@app.patch("/conversations/{cid}", dependencies=[Depends(require_bearer)])
async def patch_conversation(
    cid: str = Path(..., min_length=20, max_length=40),
    body: dict = Body(default_factory=dict),
):
    title = body.get("title")
    deleted = body.get("deleted")
    if title is None and deleted is None:
        raise HTTPException(status_code=400, detail="nothing to update")
    if title is not None and (not isinstance(title, str) or not title.strip()):
        raise HTTPException(status_code=400, detail="title must be a non-empty string")
    if deleted is not None and not isinstance(deleted, bool):
        raise HTTPException(status_code=400, detail="deleted must be bool")
    updated = dynamo.patch_conversation(config.USER_ID, cid, title=title, deleted=deleted)
    if updated is None:
        raise HTTPException(status_code=404, detail="not found")
    return JSONResponse(
        dynamo.floatify(
            {
                "id": updated["id"],
                "title": updated.get("title"),
                "updated_at": updated.get("updated_at"),
                "deleted_at": updated.get("deleted_at"),
            }
        )
    )


@app.delete("/conversations/{cid}", dependencies=[Depends(require_bearer)])
async def delete_conversation(
    cid: str = Path(..., min_length=20, max_length=40),
    force: bool = False,
):
    meta = dynamo.get_conversation_meta(config.USER_ID, cid)
    if not meta:
        raise HTTPException(status_code=404, detail="not found")
    if not meta.get("deleted_at") and not force:
        raise HTTPException(
            status_code=409,
            detail="conversation is not soft-deleted; pass ?force=true to bypass",
        )
    n = dynamo.hard_delete_conversation(config.USER_ID, cid)
    return JSONResponse({"id": cid, "deleted": True, "items_removed": n})


# ------------------- preferences -------------------


@app.get("/preferences", dependencies=[Depends(require_bearer)])
async def get_preferences():
    return JSONResponse(dynamo.floatify(dynamo.get_preferences(config.USER_ID)))


@app.patch("/preferences", dependencies=[Depends(require_bearer)])
async def patch_preferences(body: dict = Body(default_factory=dict)):
    updated = dynamo.update_preferences(
        config.USER_ID,
        do_not_recommend=body.get("do_not_recommend"),
        favorite_genres=body.get("favorite_genres"),
        recent_mood=body.get("recent_mood"),
    )
    return JSONResponse(dynamo.floatify(updated))


# ------------------- feedback -------------------


@app.post(
    "/conversations/{cid}/feedback",
    dependencies=[Depends(require_bearer)],
)
async def post_feedback(
    cid: str = Path(..., min_length=1, max_length=64),
    body: dict = Body(default_factory=dict),
) -> JSONResponse:
    """Stamp the latest assistant message in cid with thumbs up/down.

    Body: {"value": "up" | "down" | ""}  ('' clears the prior vote).
    Single-tenant — no per-user authorization beyond the bearer.
    """
    value = (body.get("value") or "").strip().lower()
    if value not in ("up", "down", ""):
        raise HTTPException(status_code=400, detail="value must be up, down, or empty")
    # Confirm the conversation exists + is owned by USER_ID before touching messages.
    meta = dynamo.get_conversation_meta(config.USER_ID, cid)
    if not meta:
        raise HTTPException(status_code=404, detail="conversation not found")
    ok = dynamo.set_latest_assistant_feedback(cid, value)
    if not ok:
        raise HTTPException(status_code=409, detail="no assistant message yet")
    return JSONResponse({"ok": True, "value": value})
