from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from mcp_server.schemas import DocReview, ReviewAggregateResponse, ReviewQuestion

PASS_THRESHOLD = 7.0

DEFAULT_QUESTIONNAIRE: list[ReviewQuestion] = [
    ReviewQuestion(id="sections_present",   question="All expected sections are present and populated"),
    ReviewQuestion(id="code_correct",       question="Code examples are syntactically correct and runnable"),
    ReviewQuestion(id="instructions_clear", question="Instructions are clear and complete"),
    ReviewQuestion(id="edge_cases",         question="Error handling and edge cases are documented"),
    ReviewQuestion(id="source_alignment",   question="Content aligns with the source material"),
]


def _reviews_dir(output_root: Path, project_id: str) -> Path:
    return output_root / project_id / "logs" / "reviews"


def generate_questionnaire(project_id: str, run_id: int) -> dict:
    return {
        "project_id": project_id,
        "run_id": run_id,
        "questions": [q.model_dump() for q in DEFAULT_QUESTIONNAIRE],
    }


def save_review(output_root: Path, project_id: str, review: DocReview) -> Path:
    reviews_dir = _reviews_dir(output_root, project_id)
    reviews_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    path = reviews_dir / f"review_{review.run_id}_{ts}.json"
    path.write_text(review.model_dump_json(indent=2), encoding="utf-8")
    return path


def aggregate_reviews(
    output_root: Path,
    project_id: str,
    run_id: int | None = None,
) -> ReviewAggregateResponse:
    reviews_dir = _reviews_dir(output_root, project_id)
    raw: list[dict] = []
    if reviews_dir.exists():
        for f in sorted(reviews_dir.glob("review_*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if run_id is None or data.get("run_id") == run_id:
                    raw.append(data)
            except Exception:
                continue

    if not raw:
        return ReviewAggregateResponse(
            project_id=project_id,
            run_id=run_id,
            total_reviews=0,
            average_overall=0.0,
            average_per_question={},
            pass_result=False,
            reviews=[],
        )

    avg_overall = sum(r["overall"] for r in raw) / len(raw)

    q_totals: dict[str, list[int]] = {}
    for r in raw:
        for q in r.get("questions", []):
            q_totals.setdefault(q["id"], []).append(q["score"])
    avg_per_q = {qid: sum(scores) / len(scores) for qid, scores in q_totals.items()}

    return ReviewAggregateResponse(
        project_id=project_id,
        run_id=run_id,
        total_reviews=len(raw),
        average_overall=round(avg_overall, 2),
        average_per_question={k: round(v, 2) for k, v in avg_per_q.items()},
        pass_result=avg_overall >= PASS_THRESHOLD,
        reviews=raw,
    )
