from __future__ import annotations

import datetime

from pydantic import BaseModel, Field


# --- Query ---

class QueryRequest(BaseModel):
    query: str = Field(..., min_length=3, description="Natural language question")
    top_k: int = Field(default=3, ge=1, le=50)
    threshold: float = Field(default=0.4, ge=0.0, le=1.0)
    document_ids: list[int] | None = Field(
        default=None,
        description="Optional: restrict search to specific documents",
    )


class Source(BaseModel):
    document_id: int
    filename: str
    chunk_index: int
    score: float
    text: str


class DocumentSource(BaseModel):
    """Document-level source (chunks merged by document)."""
    document_id: int
    filename: str
    score: float
    text: str
    reference_number: int


class QueryResponse(BaseModel):
    answer: str
    sources: list[DocumentSource]
    latency_ms: int


class DebugSearchRequest(BaseModel):
    query: str = Field(..., min_length=3, description="Natural language question")
    top_k: int = Field(default=3, ge=1, le=50)


class DebugSearchResult(BaseModel):
    document_id: int
    filename: str
    chunk_index: int
    score: float
    text: str


class DebugSearchResponse(BaseModel):
    query: str
    rbac_groups: list[str]
    rbac_filter_applied: bool
    top_k: int
    embedding_ms: int
    total_results: int
    results: list[DebugSearchResult]


# --- Documents ---

class DocumentResponse(BaseModel):
    id: int
    filename: str
    file_type: str
    source: str | None = None
    author: str | None = None
    department: str | None = None
    chunk_count: int
    created_at: datetime.datetime


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse]
    total: int


# --- Ingest ---

class IngestResponse(BaseModel):
    document_id: int
    filename: str
    status: str  # pending | processing | completed | failed
    message: str


class MarkdownIngestRequest(BaseModel):
    """Request to ingest raw markdown text (no file upload needed)."""
    text: str = Field(..., min_length=1, description="Raw markdown content")
    filename: str = Field(..., min_length=1, description="e.g. job_001_payment_loader.md")
    source: str = Field(default="", description="e.g. intodoc:vaxxinova")
    project_id: str = Field(default="", description="Project identifier")
    metadata: dict = Field(default_factory=dict)


# --- Health ---

class ServiceStatus(BaseModel):
    status: str  # ok | error
    message: str | None = None


class HealthResponse(BaseModel):
    status: str  # healthy | degraded | unhealthy
    database: ServiceStatus
    qdrant: ServiceStatus
    ollama: ServiceStatus


# --- Conversations ---


class ConversationMessageRequest(BaseModel):
    """Request to send a message in a conversation."""

    query: str = Field(..., min_length=1, description="User's message/question")
    top_k: int = Field(default=3, ge=1, le=50)
    threshold: float = Field(default=0.4, ge=0.0, le=1.0)


class MessageResponse(BaseModel):
    """A single message in a conversation."""

    id: int
    role: str  # "user" | "assistant"
    content: str
    sources: list[DocumentSource] | None = None
    latency_ms: int | None = None
    created_at: datetime.datetime


class ConversationResponse(BaseModel):
    """Summary of a conversation (for list view)."""

    id: int
    title: str | None = None
    message_count: int
    created_at: datetime.datetime
    updated_at: datetime.datetime


class ConversationDetailResponse(BaseModel):
    """Full conversation with all messages."""

    id: int
    title: str | None = None
    messages: list[MessageResponse]
    created_at: datetime.datetime
    updated_at: datetime.datetime


class ConversationListResponse(BaseModel):
    """Paginated list of conversations."""

    conversations: list[ConversationResponse]
    total: int


class ConversationCreateRequest(BaseModel):
    """Request to create a new conversation."""

    title: str | None = Field(default=None, max_length=200)


class ConversationUpdateRequest(BaseModel):
    """Request to update a conversation."""

    title: str = Field(..., min_length=1, max_length=200)
