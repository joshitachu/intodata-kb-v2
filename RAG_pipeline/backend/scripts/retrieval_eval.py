"""Retrieval evaluation utility — recall@k baseline.

Loads a JSON fixture of {question, expected_filename, expected_section?} items,
runs search_and_rerank against the running Qdrant stack, and reports:
  - recall@30 pre-rerank  (hybrid search candidates)
  - recall@8 post-rerank  (final result set)

Usage:
    cd RAG_pipeline/backend
    python scripts/retrieval_eval.py --fixture scripts/eval_fixtures/koopmans.json \\
        --project-id LobsterProfileExportKoopmansTest

Fixture schema (JSON array):
    [
      {
        "question": "What retry policy does the API_Contracts document describe?",
        "expected_filename": "02_api_contracts.md",
        "expected_section": "API Contracts > Retry Policy"   # optional
      },
      ...
    ]

The script connects to settings.qdrant_url and uses admin-level access
(no RBAC group restrictions) so all indexed docs are reachable.
Output is printed to stdout and can be redirected to a log file.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Allow running from the project root or backend/ without installing the package.
sys.path.insert(0, str(Path(__file__).parent.parent))

from qdrant_client import AsyncQdrantClient

from src.config import settings
from src.retrieval.search import search_and_rerank, search_similar_hybrid


async def _eval(fixture_path: Path, project_id: str | None, top_k: int) -> None:
    items = json.loads(fixture_path.read_text(encoding="utf-8"))
    if not items:
        print("Fixture is empty — nothing to evaluate.")
        return

    client = AsyncQdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None)

    # Admin-level: no group restrictions, project-scoped if project_id given.
    user_groups = ["admin"]
    project_ids = [project_id] if project_id else None

    pre_hits = 0
    post_hits = 0
    total = len(items)

    print(f"\nEvaluating {total} questions | project={project_id or 'ALL'} | top_k={top_k}")
    print("-" * 72)

    for item in items:
        question: str = item["question"]
        expected_fn: str = item["expected_filename"]
        expected_sec: str | None = item.get("expected_section")

        # Pre-rerank: hybrid search with rerank_candidates count
        candidates = await search_similar_hybrid(
            client=client,
            query=question,
            user_groups=user_groups,
            top_k=30,
            threshold=0.0,
            project_ids=project_ids,
        )
        pre_match = any(
            expected_fn in c.filename for c in candidates
        )
        if pre_match:
            pre_hits += 1

        # Post-rerank: full pipeline
        results = await search_and_rerank(
            client=client,
            query=question,
            user_groups=user_groups,
            top_k=top_k,
            project_ids=project_ids,
        )
        post_match = any(
            expected_fn in r.filename for r in results
        )
        if post_match:
            post_hits += 1

        marker = "HIT " if post_match else "MISS"
        section_info = f" | section={expected_sec!r}" if expected_sec else ""
        print(f"[{marker}] {question[:60]!r}{section_info}")
        if not post_match:
            result_files = [r.filename for r in results]
            print(f"       Expected: {expected_fn!r}")
            print(f"       Got:      {result_files}")

    print("-" * 72)
    print(f"recall@30 (pre-rerank):  {pre_hits}/{total} = {pre_hits/total:.1%}")
    print(f"recall@{top_k} (post-rerank): {post_hits}/{total} = {post_hits/total:.1%}")
    await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG retrieval evaluation")
    parser.add_argument(
        "--fixture",
        required=True,
        type=Path,
        help="Path to JSON fixture file",
    )
    parser.add_argument(
        "--project-id",
        default=None,
        help="Restrict evaluation to a specific project_id",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=8,
        help="Top-k for post-rerank results (default: 8)",
    )
    args = parser.parse_args()
    asyncio.run(_eval(args.fixture, args.project_id, args.top_k))


if __name__ == "__main__":
    main()
