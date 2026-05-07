import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  useGenerateRuns,
  useQuestionnaire,
  useReviews,
  useSubmitReview,
} from "../hooks/useProjects";

interface Props {
  projectId: string;
}

export default function ValidationReviewPanel({ projectId }: Props) {
  const { data: runsData } = useGenerateRuns(projectId);
  const runs = (runsData?.runs ?? []).filter((r) => r.status === "completed");
  const [runId, setRunId] = useState<number | null>(runs[0]?.id ?? null);
  const [reviewer, setReviewer] = useState("");
  const [overall, setOverall] = useState(7);
  const [feedback, setFeedback] = useState("");
  const [scores, setScores] = useState<Record<string, number>>({});
  const questionnaire = useQuestionnaire(projectId, runId);
  const reviews = useReviews(projectId, runId);
  const submit = useSubmitReview();

  useEffect(() => {
    if (runId == null && runs[0]) setRunId(runs[0].id);
  }, [runId, runs]);

  useEffect(() => {
    const next: Record<string, number> = {};
    questionnaire.data?.questions.forEach((q) => {
      next[q.id] = scores[q.id] || 3;
    });
    setScores(next);
  }, [questionnaire.data?.run_id]);

  const questions = questionnaire.data?.questions ?? [];
  const canSubmit = useMemo(
    () => runId != null && reviewer.trim().length > 0 && questions.every((q) => scores[q.id] >= 1 && scores[q.id] <= 5),
    [runId, reviewer, questions, scores],
  );

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!canSubmit || runId == null) return;
    submit.mutate(
      {
        projectId,
        data: {
          run_id: runId,
          reviewer: reviewer.trim(),
          overall,
          questions: questions.map((q) => ({ ...q, score: scores[q.id] })),
          feedback,
          reviewed_at: new Date().toISOString(),
        },
      },
      {
        onSuccess: () => {
          setFeedback("");
        },
      },
    );
  }

  return (
    <div className="card-dark">
      <div className="px-4 py-3 border-b border-white/[0.05] bg-surface-raised rounded-t">
        <h3 className="text-sm font-mono text-slate-400">Validation Review</h3>
      </div>
      <div className="p-4 space-y-4">
        {runs.length === 0 ? (
          <p className="text-sm text-slate-500">No completed runs available for review.</p>
        ) : (
          <>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <div>
                <label className="block font-mono uppercase tracking-wider text-xs text-slate-600 mb-1">Run</label>
                <select
                  value={runId ?? ""}
                  onChange={(e) => setRunId(e.target.value ? Number(e.target.value) : null)}
                  className="input-dark"
                >
                  {runs.map((run) => (
                    <option key={run.id} value={run.id}>#{run.id} ({run.doc_count} docs)</option>
                  ))}
                </select>
              </div>
              <div className="bg-surface-raised rounded px-3 py-2.5 border border-white/[0.07]">
                <div className="text-lg font-mono text-slate-300">
                  {reviews.data?.total_reviews ?? 0}
                </div>
                <div className="text-xs text-slate-500">Reviews</div>
              </div>
              <div className="bg-surface-raised rounded px-3 py-2.5 border border-white/[0.07]">
                <div className={`text-lg font-mono ${
                  reviews.data?.pass_result ? "text-[#a0e06f]" : "text-amber-400"
                }`}>
                  {reviews.data ? reviews.data.average_overall.toFixed(2) : "-"}
                </div>
                <div className="text-xs text-slate-500">
                  Average Overall {reviews.data?.pass_result ? "PASS" : "OPEN"}
                </div>
              </div>
            </div>

            {questions.length > 0 && (
              <form onSubmit={handleSubmit} className="space-y-3">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <label className="block font-mono uppercase tracking-wider text-xs text-slate-600 mb-1">Reviewer</label>
                    <input value={reviewer} onChange={(e) => setReviewer(e.target.value)} className="input-dark" />
                  </div>
                  <div>
                    <label className="block font-mono uppercase tracking-wider text-xs text-slate-600 mb-1">Overall</label>
                    <input
                      type="number"
                      min={1}
                      max={10}
                      value={overall}
                      onChange={(e) => setOverall(Number(e.target.value))}
                      className="input-dark"
                    />
                  </div>
                </div>
                <div className="space-y-2">
                  {questions.map((question) => (
                    <div key={question.id} className="grid grid-cols-[1fr_6rem_5rem] gap-3 items-center text-sm">
                      <span className="text-slate-400">{question.question}</span>
                      <input
                        type="range"
                        min={1}
                        max={5}
                        value={scores[question.id] ?? 3}
                        onChange={(e) => setScores((prev) => ({ ...prev, [question.id]: Number(e.target.value) }))}
                      />
                      <span className="font-mono text-slate-500">
                        {scores[question.id] ?? 3}/5
                        {reviews.data?.average_per_question[question.id] != null && (
                          <span className="block text-slate-600">
                            avg {reviews.data.average_per_question[question.id].toFixed(2)}
                          </span>
                        )}
                      </span>
                    </div>
                  ))}
                </div>
                <textarea
                  value={feedback}
                  onChange={(e) => setFeedback(e.target.value)}
                  className="input-dark min-h-20"
                  placeholder="Review notes"
                />
                <button
                  type="submit"
                  disabled={!canSubmit || submit.isPending}
                  className={`px-4 py-2 text-sm rounded font-medium transition-all ${
                    canSubmit && !submit.isPending
                      ? "bg-accent-primary text-surface-bg hover:bg-accent-primary/90"
                      : "bg-white/[0.05] text-slate-600 cursor-not-allowed"
                  }`}
                >
                  {submit.isPending ? "Submitting..." : "Submit Review"}
                </button>
                {submit.isError && <span className="ml-3 text-sm text-red-400">Failed to submit review</span>}
              </form>
            )}
          </>
        )}
      </div>
    </div>
  );
}
