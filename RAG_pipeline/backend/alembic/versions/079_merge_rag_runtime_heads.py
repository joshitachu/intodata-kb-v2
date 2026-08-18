"""Merge the RAG-revision and runtime-topology migration branches.

Revision ID: 079_merge_rag_runtime_heads
Revises: 077_rag_knowledge_revisions, 078_runtime_static_correlation
"""

from __future__ import annotations


revision = "079_merge_rag_runtime_heads"
down_revision = (
    "077_rag_knowledge_revisions",
    "078_runtime_static_correlation",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Merge-only revision; both parent schemas are already complete."""


def downgrade() -> None:
    """Split back to the two parent heads without changing either schema."""
