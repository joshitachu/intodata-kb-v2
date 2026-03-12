"""Report generation for benchmark results."""

from __future__ import annotations

from src.db.models import BenchmarkRun


def format_run_summary(run: BenchmarkRun) -> str:
    """Format a benchmark run as a readable text summary."""
    lines = [
        f"Benchmark Run: {run.tag}",
        f"  Corpus: {run.corpus_name} v{run.corpus_version}",
        f"  Models: LLM={run.llm_model}, Embed={run.embed_model}, Reranker={run.reranker_model or 'none'}",
        f"  Questions: {run.num_questions}",
        "",
        "Metrics:",
        f"  Avg Precision@5: {run.avg_precision_at_5:.3f}" if run.avg_precision_at_5 is not None else "  Avg Precision@5: N/A",
        f"  Avg Relevance:   {run.avg_relevance_score:.2f}/5" if run.avg_relevance_score is not None else "  Avg Relevance:   N/A",
        f"  Avg Latency:     {run.avg_latency_ms:.0f}ms" if run.avg_latency_ms is not None else "  Avg Latency:     N/A",
        "",
        "Cost:",
        f"  Total Cost:  ${run.total_cost_usd:.4f}",
        f"  Tokens In:   {run.total_input_tokens:,}",
        f"  Tokens Out:  {run.total_output_tokens:,}",
        f"  Date:        {run.created_at.isoformat() if run.created_at else 'unknown'}",
    ]

    # Per-question breakdown
    if run.results:
        lines.append("")
        lines.append("Per-Question Results:")
        lines.append(f"  {'ID':<6} {'P@5':>6} {'Rel':>5} {'Lat':>7} {'Category':<12} {'Difficulty':<8}")
        lines.append("  " + "-" * 52)
        for r in run.results:
            lines.append(
                f"  {r.get('id', '?'):<6} "
                f"{r.get('precision_at_5', 0):>6.2f} "
                f"{r.get('relevance_score', 0):>5} "
                f"{r.get('latency_ms', 0):>6}ms "
                f"{r.get('category', ''):<12} "
                f"{r.get('difficulty', ''):<8}"
            )

    return "\n".join(lines)


def compare_runs(run_a: BenchmarkRun, run_b: BenchmarkRun) -> str:
    """Compare two benchmark runs side by side."""
    def delta(a: float | None, b: float | None, higher_is_better: bool = True) -> str:
        if a is None or b is None:
            return "N/A"
        diff = b - a
        sign = "+" if diff > 0 else ""
        indicator = ""
        if higher_is_better:
            indicator = " better" if diff > 0 else " worse" if diff < 0 else ""
        else:
            indicator = " better" if diff < 0 else " worse" if diff > 0 else ""
        return f"{sign}{diff:.3f}{indicator}"

    lines = [
        f"Comparison: {run_a.tag} vs {run_b.tag}",
        "",
        f"  {'Metric':<20} {run_a.tag:>12} {run_b.tag:>12} {'Delta':>16}",
        "  " + "-" * 64,
        f"  {'Avg P@5':<20} {(run_a.avg_precision_at_5 or 0):>12.3f} {(run_b.avg_precision_at_5 or 0):>12.3f} {delta(run_a.avg_precision_at_5, run_b.avg_precision_at_5):>16}",
        f"  {'Avg Relevance':<20} {(run_a.avg_relevance_score or 0):>12.2f} {(run_b.avg_relevance_score or 0):>12.2f} {delta(run_a.avg_relevance_score, run_b.avg_relevance_score):>16}",
        f"  {'Avg Latency (ms)':<20} {(run_a.avg_latency_ms or 0):>12.0f} {(run_b.avg_latency_ms or 0):>12.0f} {delta(run_a.avg_latency_ms, run_b.avg_latency_ms, higher_is_better=False):>16}",
        f"  {'Total Cost ($)':<20} {run_a.total_cost_usd:>12.4f} {run_b.total_cost_usd:>12.4f} {delta(run_a.total_cost_usd, run_b.total_cost_usd, higher_is_better=False):>16}",
    ]
    return "\n".join(lines)
