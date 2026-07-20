"""Formatting for recalled memory supplied to model-backed capabilities."""

from __future__ import annotations


def build_memory_context(
    user_memories: list[str],
    quality_insights: list[str],
    corrections: list[str] | None = None,
    project_memories: list[str] | None = None,
) -> str | None:
    """Format past-session memory as an explicitly separated context section."""

    has_any = bool(project_memories or user_memories or corrections or quality_insights)
    if not has_any:
        return None
    parts: list[str] = [
        "## Background knowledge from previous sessions",
        "The following was learned in PAST sessions. "
        "It is NOT part of the current conversation and should NOT be described as such.",
    ]
    if project_memories:
        parts.append("### Project knowledge (past sessions)")
        parts.extend(f"- {memory}" for memory in project_memories)
    if user_memories:
        parts.append("### Remembered facts about this user (past sessions)")
        parts.extend(f"- {memory}" for memory in user_memories)
    if corrections:
        parts.append("### Known corrections (override document content)")
        parts.extend(f"- {correction}" for correction in corrections)
    if quality_insights:
        parts.append("### RAG quality signals (system-internal, not conversation)")
        parts.extend(f"- {insight}" for insight in quality_insights)
    return "\n".join(parts)
