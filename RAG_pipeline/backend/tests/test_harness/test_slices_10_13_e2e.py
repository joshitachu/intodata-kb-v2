from __future__ import annotations

from datetime import UTC, datetime
from src.generation.agent_loop import RuntimeLoopOutcome, RuntimeProgressEvent
from src.harness.evidence import query_runtime_evidence


async def test_runtime_evidence_enforces_requested_utc_window(
    db_session, monkeypatch
) -> None:
    seen = []

    async def execute(messages, **_kwargs):  # noqa: ANN001
        seen.extend(messages)
        return RuntimeLoopOutcome(
            final_answer="One current failure was found.",
            used_runtime_data=True,
            progress_events=[
                RuntimeProgressEvent(
                    event_type="runtime_tool_result",
                    payload={
                        "tool_name": "detect_failures",
                        "success": True,
                        "data": [
                {
                    "execution_id": "old",
                    "job_name": "nightly",
                    "error_summary": "old timeout",
                    "failed_at": "2026-07-03T07:20:39Z",
                },
                {
                    "execution_id": "current",
                    "job_name": "nightly",
                    "error_summary": "current timeout",
                    "failed_at": "2026-07-11T12:00:00Z",
                },
                        ],
                    },
                )
            ],
        )

    monkeypatch.setattr("src.harness.evidence.run_runtime_tool_loop", execute)
    result = await query_runtime_evidence(
        question="What failed in the past 24 hours?",
        project_id="project-a",
        project_scope=["project-a"],
        db=db_session,
        since=datetime(2026, 7, 11, 0, 0, tzinfo=UTC),
        until=datetime(2026, 7, 12, 0, 0, tzinfo=UTC),
    )

    assert [item.id for item in result.items[1:]] == ["runtime-failure:current"]
    system_prompt = next(message["content"] for message in seen if message["role"] == "system")
    assert "since=2026-07-11T00:00:00+00:00" in system_prompt
    assert "until=2026-07-12T00:00:00+00:00" in system_prompt
