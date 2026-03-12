"""Benchmark CLI entry point.

Usage:
    python -m benchmark run [--corpus corpus/default.json] [--tag baseline] [--skip-judge]
    python -m benchmark list
    python -m benchmark show <run_id>
    python -m benchmark compare <run_id_a> <run_id_b>
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qdrant_client import AsyncQdrantClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from src.config import settings
from src.db.models import BenchmarkRun

from benchmark.runner import run_benchmark
from benchmark.report import format_run_summary, compare_runs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("benchmark")

BENCHMARK_DIR = Path(__file__).resolve().parent


async def get_db_session() -> tuple[async_sessionmaker, AsyncQdrantClient]:
    """Create database session and Qdrant client."""
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    qdrant = AsyncQdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None)
    return session_factory, qdrant


async def cmd_run(args: argparse.Namespace) -> None:
    """Run a benchmark."""
    corpus_path = Path(args.corpus)
    if not corpus_path.is_absolute():
        corpus_path = BENCHMARK_DIR / corpus_path

    if not corpus_path.exists():
        logger.error(f"Corpus file not found: {corpus_path}")
        sys.exit(1)

    session_factory, qdrant = await get_db_session()
    async with session_factory() as db:
        run = await run_benchmark(
            corpus_path=corpus_path,
            tag=args.tag,
            qdrant=qdrant,
            db=db,
            skip_judge=args.skip_judge,
        )
        await db.commit()

        print("\n" + format_run_summary(run))
        print(f"\nRun ID: {run.id}")


async def cmd_list(args: argparse.Namespace) -> None:
    """List all benchmark runs."""
    session_factory, _ = await get_db_session()
    async with session_factory() as db:
        result = await db.execute(
            select(BenchmarkRun).order_by(BenchmarkRun.created_at.desc())
        )
        runs = result.scalars().all()

        if not runs:
            print("No benchmark runs found.")
            return

        print(f"{'ID':>4}  {'Tag':<20} {'Questions':>9} {'P@5':>6} {'Rel':>5} {'Lat':>7} {'Cost':>8}  {'Date':<20}")
        print("-" * 90)
        for r in runs:
            date_str = r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "unknown"
            print(
                f"{r.id:>4}  {r.tag:<20} {r.num_questions:>9} "
                f"{(r.avg_precision_at_5 or 0):>6.3f} "
                f"{(r.avg_relevance_score or 0):>5.2f} "
                f"{(r.avg_latency_ms or 0):>6.0f}ms "
                f"${r.total_cost_usd:>7.4f}  "
                f"{date_str}"
            )


async def cmd_show(args: argparse.Namespace) -> None:
    """Show details of a benchmark run."""
    session_factory, _ = await get_db_session()
    async with session_factory() as db:
        run = await db.get(BenchmarkRun, args.run_id)
        if not run:
            print(f"Run {args.run_id} not found.")
            sys.exit(1)
        print(format_run_summary(run))


async def cmd_compare(args: argparse.Namespace) -> None:
    """Compare two benchmark runs."""
    session_factory, _ = await get_db_session()
    async with session_factory() as db:
        run_a = await db.get(BenchmarkRun, args.run_a)
        run_b = await db.get(BenchmarkRun, args.run_b)
        if not run_a:
            print(f"Run {args.run_a} not found.")
            sys.exit(1)
        if not run_b:
            print(f"Run {args.run_b} not found.")
            sys.exit(1)
        print(compare_runs(run_a, run_b))


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG Benchmark Tool")
    sub = parser.add_subparsers(dest="command", required=True)

    # run
    p_run = sub.add_parser("run", help="Run a benchmark")
    p_run.add_argument("--corpus", default="corpus/default.json", help="Path to corpus JSON file")
    p_run.add_argument("--tag", default="baseline", help="Tag for this run")
    p_run.add_argument("--skip-judge", action="store_true", help="Skip LLM-as-judge scoring")

    # list
    sub.add_parser("list", help="List all benchmark runs")

    # show
    p_show = sub.add_parser("show", help="Show details of a benchmark run")
    p_show.add_argument("run_id", type=int)

    # compare
    p_cmp = sub.add_parser("compare", help="Compare two benchmark runs")
    p_cmp.add_argument("run_a", type=int)
    p_cmp.add_argument("run_b", type=int)

    args = parser.parse_args()

    cmd_map = {
        "run": cmd_run,
        "list": cmd_list,
        "show": cmd_show,
        "compare": cmd_compare,
    }
    asyncio.run(cmd_map[args.command](args))


if __name__ == "__main__":
    main()
