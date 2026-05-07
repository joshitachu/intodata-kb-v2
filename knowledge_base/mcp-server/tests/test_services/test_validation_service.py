"""Tests for validation_service (US-105)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_server.schemas import DocReview, ReviewQuestion
from mcp_server.services import validation_service


def _make_review(run_id: int = 1, overall: int = 8) -> DocReview:
    return DocReview(
        run_id=run_id,
        reviewer="tester@example.com",
        overall=overall,
        questions=[
            ReviewQuestion(id="sections_present", question="All sections present", score=4),
            ReviewQuestion(id="code_correct", question="Code correct", score=5),
        ],
        feedback="Looks good",
        reviewed_at="2026-05-05T10:00:00Z",
    )


def test_submit_review_writes_json(tmp_path: Path):
    review = _make_review()
    path = validation_service.save_review(tmp_path, "proj1", review)

    assert path.exists()
    data = json.loads(path.read_text())
    assert data["run_id"] == 1
    assert data["reviewer"] == "tester@example.com"
    assert data["overall"] == 8
    assert len(data["questions"]) == 2


def test_aggregate_pass_when_score_above_threshold(tmp_path: Path):
    for i in range(3):
        review = _make_review(run_id=1, overall=8)
        review.reviewed_at = f"2026-05-05T1{i}:00:00Z"
        validation_service.save_review(tmp_path, "proj1", review)

    result = validation_service.aggregate_reviews(tmp_path, "proj1", run_id=1)

    assert result.total_reviews == 3
    assert result.average_overall == 8.0
    assert result.pass_result is True


def test_aggregate_fail_when_score_below_threshold(tmp_path: Path):
    for i in range(2):
        review = _make_review(run_id=2, overall=5)
        review.reviewed_at = f"2026-05-05T1{i}:00:00Z"
        validation_service.save_review(tmp_path, "proj1", review)

    result = validation_service.aggregate_reviews(tmp_path, "proj1", run_id=2)

    assert result.total_reviews == 2
    assert result.pass_result is False


def test_questionnaire_returns_default_questions():
    q = validation_service.generate_questionnaire("proj1", run_id=42)

    assert q["project_id"] == "proj1"
    assert q["run_id"] == 42
    ids = [item["id"] for item in q["questions"]]
    assert "sections_present" in ids
    assert "source_alignment" in ids
    assert len(q["questions"]) == 5
