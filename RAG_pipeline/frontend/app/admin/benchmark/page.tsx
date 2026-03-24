"use client";

import { useCallback, useEffect, useState } from "react";
import { ArrowLeft } from "lucide-react";

// --- Types ---

interface BenchmarkRunSummary {
  id: number;
  tag: string | null;
  corpus_name: string | null;
  corpus_version: string | null;
  llm_model: string | null;
  embed_model: string | null;
  reranker_model: string | null;
  num_questions: number | null;
  avg_precision_at_5: number | null;
  avg_relevance_score: number | null;
  avg_latency_ms: number | null;
  total_input_tokens: number | null;
  total_output_tokens: number | null;
  total_cost_usd: number | null;
  created_at: string | null;
}

interface QuestionResult {
  question: string;
  expected_answer?: string;
  actual_answer?: string;
  precision_at_5?: number | null;
  relevance_score?: number | null;
  latency_ms?: number | null;
  cost_usd?: number | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  sources?: string[];
}

interface BenchmarkRunDetail extends BenchmarkRunSummary {
  results: QuestionResult[] | null;
}

// --- Helpers ---

function formatNumber(n: number): string {
  return n.toLocaleString();
}

function formatCost(n: number): string {
  return `$${n.toFixed(4)}`;
}

function formatFloat(n: number | null | undefined, decimals = 2): string {
  if (n == null) return "-";
  return n.toFixed(decimals);
}

function relevanceColor(score: number | null | undefined): string {
  if (score == null) return "text-gray-400";
  if (score >= 4) return "text-[#a0e06f] font-semibold";
  if (score >= 3) return "text-yellow-600 font-semibold";
  return "text-red-600 font-semibold";
}

// --- Component ---

type View = "list" | "detail";

export default function AdminBenchmarkPage() {
  const [view, setView] = useState<View>("list");
  const [loading, setLoading] = useState(true);
  const [runs, setRuns] = useState<BenchmarkRunSummary[]>([]);
  const [selectedRun, setSelectedRun] = useState<BenchmarkRunDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const fetchRuns = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/admin/benchmark/runs");
      if (res.ok) {
        const data: { runs: BenchmarkRunSummary[]; total: number } = await res.json();
        setRuns(data.runs);
      }
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchRunDetail = useCallback(async (runId: number) => {
    setDetailLoading(true);
    try {
      const res = await fetch(`/api/admin/benchmark/runs/${runId}`);
      if (res.ok) {
        const data: BenchmarkRunDetail = await res.json();
        setSelectedRun(data);
      }
    } catch {
      /* ignore */
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchRuns();
  }, [fetchRuns]);

  function handleSelectRun(runId: number) {
    setView("detail");
    fetchRunDetail(runId);
  }

  function handleBack() {
    setView("list");
    setSelectedRun(null);
  }

  // Compute summary cards from runs list
  const bestPrecision = runs.reduce(
    (best, r) => Math.max(best, r.avg_precision_at_5 ?? 0),
    0,
  );
  const bestRelevance = runs.reduce(
    (best, r) => Math.max(best, r.avg_relevance_score ?? 0),
    0,
  );
  const avgLatency =
    runs.length > 0
      ? runs.reduce((sum, r) => sum + (r.avg_latency_ms ?? 0), 0) / runs.length
      : 0;

  if (loading) {
    return <p className="text-gray-400 p-4">Loading benchmark data...</p>;
  }

  return (
    <div className="max-w-7xl mx-auto py-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Benchmark Results</h1>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-4 gap-4">
        {[
          { label: "Total Runs", value: formatNumber(runs.length) },
          { label: "Best Precision@5", value: formatFloat(bestPrecision, 3) },
          { label: "Best Relevance", value: formatFloat(bestRelevance, 2) },
          { label: "Avg Latency", value: `${formatFloat(avgLatency, 0)}ms` },
        ].map((card) => (
          <div key={card.label} className="bg-white border rounded-lg p-4">
            <p className="text-xs text-gray-500 uppercase tracking-wide">{card.label}</p>
            <p className="text-2xl font-bold mt-1">{card.value}</p>
          </div>
        ))}
      </div>

      {/* Content */}
      {view === "list" && (
        <RunsListView runs={runs} onSelectRun={handleSelectRun} />
      )}
      {view === "detail" && (
        <RunDetailView
          run={selectedRun}
          loading={detailLoading}
          onBack={handleBack}
        />
      )}
    </div>
  );
}

// ===================== RUNS LIST =====================

function RunsListView({
  runs,
  onSelectRun,
}: {
  runs: BenchmarkRunSummary[];
  onSelectRun: (id: number) => void;
}) {
  return (
    <div className="bg-white border rounded-lg p-4">
      <h3 className="text-sm font-semibold text-gray-700 mb-3">All Runs</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 border-b">
            <tr>
              <th className="text-left px-3 py-2 font-medium text-gray-600">ID</th>
              <th className="text-left px-3 py-2 font-medium text-gray-600">Tag</th>
              <th className="text-left px-3 py-2 font-medium text-gray-600">LLM</th>
              <th className="text-left px-3 py-2 font-medium text-gray-600">Embed</th>
              <th className="text-right px-3 py-2 font-medium text-gray-600">Questions</th>
              <th className="text-right px-3 py-2 font-medium text-gray-600">Precision@5</th>
              <th className="text-right px-3 py-2 font-medium text-gray-600">Relevance</th>
              <th className="text-right px-3 py-2 font-medium text-gray-600">Avg Latency</th>
              <th className="text-right px-3 py-2 font-medium text-gray-600">Cost</th>
              <th className="text-left px-3 py-2 font-medium text-gray-600">Date</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr
                key={run.id}
                className="border-b last:border-0 hover:bg-[#fe9543]/5 cursor-pointer transition-colors"
                onClick={() => onSelectRun(run.id)}
              >
                <td className="px-3 py-2 text-gray-500">{run.id}</td>
                <td className="px-3 py-2 font-medium">
                  {run.tag || "-"}
                </td>
                <td className="px-3 py-2 text-gray-600 text-xs">{run.llm_model || "-"}</td>
                <td className="px-3 py-2 text-gray-600 text-xs">{run.embed_model || "-"}</td>
                <td className="px-3 py-2 text-right text-gray-600">
                  {run.num_questions ?? "-"}
                </td>
                <td className="px-3 py-2 text-right text-gray-600">
                  {formatFloat(run.avg_precision_at_5, 3)}
                </td>
                <td className={`px-3 py-2 text-right ${relevanceColor(run.avg_relevance_score)}`}>
                  {formatFloat(run.avg_relevance_score)}
                </td>
                <td className="px-3 py-2 text-right text-gray-600">
                  {run.avg_latency_ms != null ? `${formatFloat(run.avg_latency_ms, 0)}ms` : "-"}
                </td>
                <td className="px-3 py-2 text-right text-gray-600">
                  {run.total_cost_usd != null ? formatCost(run.total_cost_usd) : "-"}
                </td>
                <td className="px-3 py-2 text-gray-500 text-xs whitespace-nowrap">
                  {run.created_at ? new Date(run.created_at).toLocaleString() : "-"}
                </td>
              </tr>
            ))}
            {runs.length === 0 && (
              <tr>
                <td colSpan={10} className="px-3 py-6 text-center text-gray-400">
                  No benchmark runs yet. Run <code>python -m benchmark run</code> to create one.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ===================== RUN DETAIL =====================

function RunDetailView({
  run,
  loading,
  onBack,
}: {
  run: BenchmarkRunDetail | null;
  loading: boolean;
  onBack: () => void;
}) {
  if (loading) {
    return <p className="text-gray-400 p-4">Loading run details...</p>;
  }
  if (!run) {
    return <p className="text-gray-400 p-4">Run not found</p>;
  }

  const results = run.results ?? [];

  return (
    <div className="space-y-6">
      {/* Back button + run info */}
      <div className="flex items-center gap-4">
        <button
          onClick={onBack}
          className="flex items-center gap-1 text-sm text-[#fe9543] hover:text-[#fe9543]/80"
        >
          <ArrowLeft size={16} />
          Back to Runs
        </button>
      </div>

      {/* Run summary card */}
      <div className="bg-white border rounded-lg p-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-xl font-bold">
              Run #{run.id}{run.tag ? ` - ${run.tag}` : ""}
            </h2>
            <p className="text-sm text-gray-500">
              {run.llm_model || "?"} / {run.embed_model || "?"}
              {run.reranker_model ? ` + ${run.reranker_model}` : ""}
              {" | "}
              {run.created_at ? new Date(run.created_at).toLocaleString() : ""}
            </p>
          </div>
          <div className="flex gap-6 text-center">
            {[
              { label: "Precision@5", value: formatFloat(run.avg_precision_at_5, 3) },
              { label: "Relevance", value: formatFloat(run.avg_relevance_score) },
              { label: "Avg Latency", value: run.avg_latency_ms != null ? `${formatFloat(run.avg_latency_ms, 0)}ms` : "-" },
              { label: "Total Cost", value: run.total_cost_usd != null ? formatCost(run.total_cost_usd) : "-" },
            ].map((stat) => (
              <div key={stat.label}>
                <p className="text-xs text-gray-500 uppercase">{stat.label}</p>
                <p className="text-lg font-semibold">{stat.value}</p>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Per-question results */}
      <div className="bg-white border rounded-lg p-4">
        <h3 className="text-sm font-semibold text-gray-700 mb-3">
          Per-Question Results ({results.length})
        </h3>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b">
              <tr>
                <th className="text-left px-3 py-2 font-medium text-gray-600">#</th>
                <th className="text-left px-3 py-2 font-medium text-gray-600">Question</th>
                <th className="text-right px-3 py-2 font-medium text-gray-600">Precision@5</th>
                <th className="text-right px-3 py-2 font-medium text-gray-600">Relevance</th>
                <th className="text-right px-3 py-2 font-medium text-gray-600">Latency</th>
                <th className="text-right px-3 py-2 font-medium text-gray-600">Cost</th>
                <th className="text-right px-3 py-2 font-medium text-gray-600">Tokens (in/out)</th>
              </tr>
            </thead>
            <tbody>
              {results.map((r, i) => (
                <tr key={i} className="border-b last:border-0">
                  <td className="px-3 py-2 text-gray-400">{i + 1}</td>
                  <td
                    className="px-3 py-2 text-gray-700 max-w-md"
                    title={r.question}
                  >
                    <div className="truncate">{r.question}</div>
                    {r.actual_answer && (
                      <div className="text-xs text-gray-400 truncate mt-0.5" title={r.actual_answer}>
                        {r.actual_answer.slice(0, 120)}...
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right text-gray-600">
                    {formatFloat(r.precision_at_5, 3)}
                  </td>
                  <td className={`px-3 py-2 text-right ${relevanceColor(r.relevance_score)}`}>
                    {formatFloat(r.relevance_score)}
                  </td>
                  <td className="px-3 py-2 text-right text-gray-600">
                    {r.latency_ms != null ? `${formatFloat(r.latency_ms, 0)}ms` : "-"}
                  </td>
                  <td className="px-3 py-2 text-right text-gray-600">
                    {r.cost_usd != null ? formatCost(r.cost_usd) : "-"}
                  </td>
                  <td className="px-3 py-2 text-right text-gray-600 text-xs">
                    {r.input_tokens != null ? formatNumber(r.input_tokens) : "-"}
                    {" / "}
                    {r.output_tokens != null ? formatNumber(r.output_tokens) : "-"}
                  </td>
                </tr>
              ))}
              {results.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-3 py-6 text-center text-gray-400">
                    No per-question results available
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
