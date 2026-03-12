"""Conversation API routes for chat-based RAG."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
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
        ))
    return result


@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    db: DBSession,
    user: CurrentUser,
    skip: int = 0,
    limit: int = Query(default=50, ge=1, le=100),
) -> ConversationListResponse:
    """List user's conversations, sorted by most recently updated."""
    # Count total
    total_result = await db.execute(
        select(func.count(Conversation.id)).where(Conversation.user_id == user.id)
    )
    total = total_result.scalar_one()

    # Get conversations with message counts
    result = await db.execute(
        select(Conversation)
        .where(Conversation.user_id == user.id)
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


@router.post("", response_model=ConversationResponse, status_code=201)
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


@router.get("/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(
    conversation_id: int,
    db: DBSession,
    user: CurrentUser,
) -> ConversationDetailResponse:
    """Get a conversation with all messages."""
    result = await db.execute(
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .options(selectinload(Conversation.messages))
    )
    conversation = result.scalar_one_or_none()

    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if conversation.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    return ConversationDetailResponse(
        id=conversation.id,
        title=conversation.title,
        messages=[
            MessageResponse(
                id=m.id,
                role=m.role,
                content=m.content,
                sources=_deserialize_sources(m.sources) if m.sources else None,
                latency_ms=m.latency_ms,
                created_at=m.created_at,
            )
            for m in conversation.messages
        ],
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


@router.patch("/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    conversation_id: int,
    request: ConversationUpdateRequest,
    db: DBSession,
    user: CurrentUser,
) -> ConversationResponse:
    """Update conversation title."""
    result = await db.execute(
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .options(selectinload(Conversation.messages))
    )
    conversation = result.scalar_one_or_none()

    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if conversation.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    conversation.title = request.title

    return ConversationResponse(
        id=conversation.id,
        title=conversation.title,
        message_count=len(conversation.messages),
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: int,
    db: DBSession,
    user: CurrentUser,
) -> None:
    """Delete a conversation and all its messages."""
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = result.scalar_one_or_none()

    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if conversation.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    await db.delete(conversation)


@router.post("/{conversation_id}/messages", response_model=MessageResponse)
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
        .where(Conversation.id == conversation_id)
        .options(selectinload(Conversation.messages))
    )
    conversation = result.scalar_one_or_none()

    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if conversation.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Get user's groups and project access for RBAC filtering
    user_groups = [role.name for role in user.roles]
    project_ids = await get_user_project_ids(user, db)

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
    )

    return response


@router.post("/{conversation_id}/messages/stream")
async def send_message_stream(
    conversation_id: int,
    request: ConversationMessageRequest,
    db: DBSession,
    user: CurrentUser,
    qdrant: QdrantClient,
) -> StreamingResponse:
    """Send a message and stream the RAG-powered response via SSE.

    Returns Server-Sent Events with:
    - event: sources → JSON array of DocumentSource objects
    - event: token   → individual LLM tokens (JSON-encoded string)
    - event: done    → {message_id, latency_ms}
    """
    # Load conversation with messages
    result = await db.execute(
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .options(selectinload(Conversation.messages))
    )
    conversation = result.scalar_one_or_none()

    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if conversation.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Get user's groups and project access for RBAC filtering
    user_groups = [role.name for role in user.roles]
    project_ids = await get_user_project_ids(user, db)

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
            ):
                yield f"event: {event_type}\ndata: {data}\n\n"
        except Exception as e:
            logger.error("Conversation stream failed: %s", e, exc_info=True)
            yield f"event: error\ndata: {json.dumps(str(e))}\n\n"
            yield "event: done\ndata: {}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
