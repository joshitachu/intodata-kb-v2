"""Rebuild every stored sparse vector with the current token hash.

Why this exists: the sparse index is only useful while ingestion and query time
agree on the token -> index mapping. Changing that mapping (see
``SPARSE_HASH_VERSION`` in :mod:`src.retrieval.sparse_embedder`) makes every
sparse vector already in Qdrant stale -- they still have the right *shape*, so
nothing errors, the sparse half of hybrid search just silently stops matching.
That is the worst possible failure mode, which is why this is a deliberate,
operator-run step rather than something a deployment does to itself.

It rebuilds vectors ONLY. The dense vectors, the payloads, the point ids and the
chunking are untouched: the chunk text is already stored in the ``text`` payload
field, so the sparse vector is recomputed from that and written back with
``update_vectors``. There is no re-embedding cost, no LLM call, and no document
re-processing -- which is what makes running it cheap enough that an operator has
no excuse to skip it.

Usage (inside the backend container, or any environment with QDRANT_URL set):

    # See how much work there is, change nothing:
    python -m src.retrieval.reindex_sparse --dry-run

    # Rebuild every point's sparse vector:
    python -m src.retrieval.reindex_sparse

    # Larger batches on a fast link:
    python -m src.retrieval.reindex_sparse --batch-size 1000

Safe to interrupt and safe to re-run: each batch is an idempotent overwrite of
the same point ids, so a second pass produces byte-identical vectors.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import PointVectors
from qdrant_client.models import SparseVector as QdrantSparseVector

from src.config import settings
from src.retrieval.sparse_embedder import SPARSE_HASH_VERSION, embed_text_sparse
from src.retrieval.vector_store import COLLECTION_NAME

logger = logging.getLogger(__name__)

#: Points fetched (and written back) per round trip. 500 keeps the payload of a
#: scroll response comfortably inside a default Qdrant response limit while
#: still amortising the round trip over a useful number of points.
DEFAULT_BATCH_SIZE = 500


async def reindex_sparse_vectors(
    client: AsyncQdrantClient,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    dry_run: bool = False,
) -> tuple[int, int]:
    """Recompute the sparse vector of every point from its stored chunk text.

    Returns ``(points_seen, points_rewritten)``. A point with no ``text``
    payload is counted as seen and skipped rather than zeroed: an empty sparse
    vector would remove the chunk from sparse recall entirely, which is a larger
    change than this tool is allowed to make.
    """

    seen = 0
    rewritten = 0
    offset = None
    started = time.perf_counter()

    while True:
        points, offset = await client.scroll(
            collection_name=COLLECTION_NAME,
            limit=batch_size,
            offset=offset,
            with_payload=["text"],
            with_vectors=False,
        )
        if not points:
            break

        updates: list[PointVectors] = []
        for point in points:
            seen += 1
            text = (point.payload or {}).get("text")
            if not text:
                logger.warning("Point %s has no text payload; sparse vector left as is", point.id)
                continue
            sparse = embed_text_sparse(text)
            updates.append(
                PointVectors(
                    id=point.id,
                    vector={
                        "sparse": QdrantSparseVector(
                            indices=sparse.indices, values=sparse.values
                        )
                    },
                )
            )

        if updates and not dry_run:
            await client.update_vectors(collection_name=COLLECTION_NAME, points=updates)
        rewritten += len(updates)
        logger.info(
            "Sparse reindex progress: seen=%d rewritten=%d elapsed=%.1fs",
            seen,
            rewritten,
            time.perf_counter() - started,
        )

        if offset is None:
            break

    return seen, rewritten


async def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.retrieval.reindex_sparse",
        description=(
            "Rebuild every stored sparse vector with the current token hash "
            f"(SPARSE_HASH_VERSION={SPARSE_HASH_VERSION})."
        ),
    )
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count the points that would be rewritten and write nothing.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    # Passed through exactly as configured, `or None` deliberately absent. Every
    # other client in the codebase (api/dependencies.py, onboarding_service.py,
    # scripts/) hands the setting straight to the client, and a blank key is the
    # normal local configuration. Collapsing "" to None changes the request the
    # client builds and Qdrant answers it with 401, so this script failed against
    # the very deployment it was written for while the app beside it worked.
    client = AsyncQdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
    try:
        seen, rewritten = await reindex_sparse_vectors(
            client, batch_size=args.batch_size, dry_run=args.dry_run
        )
    finally:
        await client.close()

    logger.info(
        "Sparse reindex %s: collection=%s points_seen=%d points_rewritten=%d version=%d",
        "planned (dry run, nothing written)" if args.dry_run else "complete",
        COLLECTION_NAME,
        seen,
        rewritten,
        SPARSE_HASH_VERSION,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    sys.exit(asyncio.run(_main()))
