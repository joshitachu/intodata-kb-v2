"""Conversation API routes for chat-based RAG."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query
from pydantic import BaseModel

from src.api.errors import api_error
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload

from src.api.dependencies import CurrentUser, DBSession, QdrantClient
from src.db.models import Conversation, ConversationMessage
from src.generation.conversational_rag import (
    run_conversational_rag,
    run_conversational_rag_stream,
)
from src.rbac.permissions import get_user_project_ids
from src.schemas import (
    ConversationCreateRequest,
    ConversationDetailResponse,
    ConversationListResponse,
    ConversationMessageRequest,
    ConversationResponse,
    ConversationUpdateRequest,
    DocumentSource,
    MessageResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversations", tags=["conversations"])


def _deserialize_sources(raw: list[dict]) -> list[DocumentSource]:
    """Deserialize sources from DB, handling both old Source and new DocumentSource formats."""
    result = []
    for i, s in enumerate(raw, 1):
        result.append(DocumentSource(
            document_id=s["document_id"],
            filename=s["filename"],
            score=s["score"],
            text=s["text"],
            reference_number=s.get("reference_number", i),
            chunk_indexes=s.get("chunk_indexes", []),
            source_layer=s.get("source_layer"),
            section=s.get("section"),
            unit=s.get("unit"),
            group=s.get("group"),
            display_name=s.get("display_name"),
            artifact_path=s.get("artifact_path"),
            visibility=s.get("visibility"),
            run_id=s.get("run_id"),
            project_id=s.get("project_id"),
        ))
    return result


@router.get("", response_model=ConversationListResponse, summary="List conversations")
async def list_conversations(
    db: DBSession,
    user: CurrentUser,
    skip: int = 0,
    limit: int = Query(default=50, ge=1, le=100),
) -> ConversationListResponse:
    """List user's conversations, sorted by most recently updated."""
    # Count total
    total_result = await db.execute(
        select(func.count(Conversation.id))
        .where(Conversation.user_id == user.id, Conversation.deleted_at.is_(None))
    )
    total = total_result.scalar_one()

    # Get conversations with message counts
    result = await db.execute(
        select(Conversation)
        .where(Conversation.user_id == user.id, Conversation.deleted_at.is_(None))
        .order_by(Conversation.updated_at.desc())
        .offset(skip)
        .limit(limit)
        .options(selectinload(Conversation.messages))
    )
    conversations = result.scalars().all()

    return ConversationListResponse(
        conversations=[
            ConversationResponse(
                id=c.id,
                title=c.title,
                message_count=len(c.messages),
                created_at=c.created_at,
                updated_at=c.updated_at,
            )
            for c in conversations
        ],
        total=total,
    )


@router.post("", response_model=ConversationResponse, status_code=201, summary="Create a conversation")
async def create_conversation(
    db: DBSession,
    user: CurrentUser,
    request: ConversationCreateRequest | None = None,
) -> ConversationResponse:
    """Create a new conversation."""
    title = request.title if request else None
    conversation = Conversation(user_id=user.id, title=title)
    db.add(conversation)
    await db.flush()

    return ConversationResponse(
        id=conversation.id,
        title=conversation.title,
        message_count=0,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


@router.get("/{conversation_id}", response_model=ConversationDetailResponse, summary="Get a conversation with its messages")
async def get_conversation(
    conversation_id: int,
    db: DBSession,
    user: CurrentUser,
) -> ConversationDetailResponse:
    """Get a conversation with all messages."""
    result = await db.execute(
        select(Conversation)
        .where(Conversation.id == conversation_id, Conversation.deleted_at.is_(None))
        .options(selectinload(Conversation.messages))
    )
    conversation = result.scalar_one_or_none()

    if conversation is None:
        raise api_error(404, "not_found", "Conversation not found")

    if conversation.user_id != user.id:
        raise api_error(403, "forbidden", "Not authorized")

    return ConversationDetailResponse(
        id=conversation.id,
        title=conversation.title,
        messages=[
            MessageResponse(
                id=m.id,
                role=m.role,
                content=m.content,
                sources=_deserialize_sources(m.sources) if m.sources else None,
                image_data=m.image_data,
                latency_ms=m.latency_ms,
                status=m.status,
                error_message=m.error_message,
                primary_source_layer=m.primary_source_layer,
                fallback_used=m.fallback_used,
                doc_gap=m.doc_gap,
                created_at=m.created_at,
            )
            for m in conversation.messages
        ],
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


@router.patch("/{conversation_id}", response_model=ConversationResponse, summary="Rename a conversation")
async def update_conversation(
    conversation_id: int,
    request: ConversationUpdateRequest,
    db: DBSession,
    user: CurrentUser,
) -> ConversationResponse:
    """Update conversation title."""
    result = await db.execute(
        select(Conversation)
        .where(Conversation.id == conversation_id, Conversation.deleted_at.is_(None))
        .options(selectinload(Conversation.messages))
    )
    conversation = result.scalar_one_or_none()

    if conversation is None:
        raise api_error(404, "not_found", "Conversation not found")

    if conversation.user_id != user.id:
        raise api_error(403, "forbidden", "Not authorized")

    conversation.title = request.title

    return ConversationResponse(
        id=conversation.id,
        title=conversation.title,
        message_count=len(conversation.messages),
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


@router.delete("/{conversation_id}", status_code=204, summary="Delete a conversation and its messages")
async def delete_conversation(
    conversation_id: int,
    db: DBSession,
    user: CurrentUser,
) -> None:
    """Soft-delete a conversation and all its messages."""
    result = await db.execute(
        select(Conversation)
        .where(Conversation.id == conversation_id, Conversation.deleted_at.is_(None))
    )
    conversation = result.scalar_one_or_none()

    if conversation is None:
        raise api_error(404, "not_found", "Conversation not found")

    if conversation.user_id != user.id:
        raise api_error(403, "forbidden", "Not authorized")

    now = datetime.now(timezone.utc)
    conversation.deleted_at = now
    # Cascade soft-delete to all messages
    await db.execute(
        update(ConversationMessage)
        .where(
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.deleted_at.is_(None),
        )
        .values(deleted_at=now)
    )


@router.post("/{conversation_id}/messages", response_model=MessageResponse, summary="Send a message and get a RAG response")
async def send_message(
    conversation_id: int,
    request: ConversationMessageRequest,
    db: DBSession,
    user: CurrentUser,
    qdrant: QdrantClient,
) -> MessageResponse:
    """Send a message and get a RAG-powered response."""
    # Load conversation with messages
    result = await db.execute(
        select(Conversation)
        .where(Conversation.id == conversation_id, Conversation.deleted_at.is_(None))
        .options(selectinload(Conversation.messages))
    )
    conversation = result.scalar_one_or_none()

    if conversation is None:
        raise api_error(404, "not_found", "Conversation not found")

    if conversation.user_id != user.id:
        raise api_error(403, "forbidden", "Not authorized")

    # Get user's groups and project access for RBAC filtering
    user_groups = [role.name for role in user.roles]
    project_ids = await get_user_project_ids(user, db)
    if request.project_id:
        if project_ids is not None and request.project_id not in project_ids:
            raise api_error(403, "forbidden", "No access to this project")
        project_ids = [request.project_id]

    # Auto-title from first user message if conversation has no title yet
    if not conversation.title and not conversation.messages:
        conversation.title = request.query[:80].strip()

    # Run conversational RAG pipeline
    response = await run_conversational_rag(
        conversation=conversation,
        query=request.query,
        user_groups=user_groups,
        qdrant=qdrant,
        db=db,
        top_k=request.top_k,
        threshold=request.threshold,
        user_id=str(user.id),
        project_ids=project_ids,
        images=request.images,
    )

    return response


@router.post("/{conversation_id}/messages/stream", summary="Send a message and stream the RAG response via SSE")
async def send_message_stream(
    conversation_id: int,
    request: ConversationMessageRequest,
    db: DBSession,
    user: CurrentUser,
    qdrant: QdrantClient,
) -> StreamingResponse:
    """Send a message and stream the RAG-powered response via SSE.

    Returns Server-Sent Events with:
    - event: step    → optional read-only agent tool progress
    - event: sources → JSON array of DocumentSource objects
    - event: token   → individual LLM tokens (JSON-encoded string)
    - event: done    → {message_id, latency_ms}
    """
    # Load conversation with messages
    result = await db.execute(
        select(Conversation)
        .where(Conversation.id == conversation_id, Conversation.deleted_at.is_(None))
        .options(selectinload(Conversation.messages))
    )
    conversation = result.scalar_one_or_none()

    if conversation is None:
        raise api_error(404, "not_found", "Conversation not found")

    if conversation.user_id != user.id:
        raise api_error(403, "forbidden", "Not authorized")

    # Get user's groups and project access for RBAC filtering
    user_groups = [role.name for role in user.roles]
    project_ids = await get_user_project_ids(user, db)
    if request.project_id:
        if project_ids is not None and request.project_id not in project_ids:
            raise api_error(403, "forbidden", "No access to this project")
        project_ids = [request.project_id]

    # Auto-title from first user message if conversation has no title yet
    if not conversation.title and not conversation.messages:
        conversation.title = request.query[:80].strip()
        await db.flush()

    async def event_generator():
        try:
            async for event_type, data in run_conversational_rag_stream(
                conversation=conversation,
                query=request.query,
                user_groups=user_groups,
                qdrant=qdrant,
                db=db,
                top_k=request.top_k,
                threshold=request.threshold,
                user_id=str(user.id),
                project_ids=project_ids,
                images=request.images,
            ):
                yield f"event: {event_type}\ndata: {data}\n\n"
        except Exception as e:
            logger.error("Conversation stream failed: %s", e, exc_info=True)
            safe_message = "Chat response failed. Please try again."
            failed_msg = ConversationMessage(
                conversation_id=conversation.id,
                role="assistant",
                content="",
                status="failed",
                error_message=safe_message,
            )
            db.add(failed_msg)
            await db.flush()
            await db.commit()
            yield f"event: error\ndata: {json.dumps(safe_message)}\n\n"
            yield f"event: done\ndata: {json.dumps({'message_id': failed_msg.id, 'latency_ms': 0})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


class SaveExchangeRequest(BaseModel):
    user_message: str
    assistant_message: str
    latency_ms: int | None = None


@router.post("/{conversation_id}/messages/save", response_model=MessageResponse, summary="Save a pre-generated exchange")
async def save_exchange(
    conversation_id: int,
    request: SaveExchangeRequest,
    db: DBSession,
    user: CurrentUser,
) -> MessageResponse:
    """Save a pre-generated Q&A exchange (e.g. Deep Dive) without running RAG."""
    result = await db.execute(
        select(Conversation)
        .where(Conversation.id == conversation_id, Conversation.deleted_at.is_(None))
        .options(selectinload(Conversation.messages))
    )
    conversation = result.scalar_one_or_none()

    if conversation is None:
        raise api_error(404, "not_found", "Conversation not found")

    if conversation.user_id != user.id:
        raise api_error(403, "forbidden", "Not authorized")

    if not conversation.title and not conversation.messages:
        conversation.title = request.user_message[:80].strip()

    user_msg = ConversationMessage(
        conversation_id=conversation_id,
        role="user",
        content=request.user_message,
    )
    db.add(user_msg)
    await db.flush()

    assistant_msg = ConversationMessage(
        conversation_id=conversation_id,
        role="assistant",
        content=request.assistant_message,
        latency_ms=request.latency_ms,
    )
    db.add(assistant_msg)
    await db.flush()
    await db.commit()

    return MessageResponse(
        message_id=assistant_msg.id,
        content=request.assistant_message,
        sources=[],
        latency_ms=request.latency_ms or 0,
    )
