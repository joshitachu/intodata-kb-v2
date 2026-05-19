import { useState } from "react";
import { useScan, useGenerate, usePushToRag, useResetStatus, useDeleteProject } from "../hooks/useProjects";

interface Props {
  projectId: string;
  status: string;
  onAction: (projectId: string) => void;
}

export default function ActionButtons({ projectId, status, onAction }: Props) {
  const scan = useScan();
  const generate = useGenerate();
  const push = usePushToRag();
  const resetStatus = useResetStatus();
  const deleteProject = useDeleteProject();

  const [resumeGenerate, setResumeGenerate] = useState(true);

  const busy = status === "scanning" || status === "generating";

  return (
    <div className="card-actions flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
      {/* Scan */}
      <button
        className="btn-icon-action"
        disabled={busy || scan.isPending}
        title="Re-scan"
        onClick={() => { onAction(projectId); scan.mutate({ projectId, force: true }); }}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21.5 2v6h-6M2.5 22v-6h6M2 11.5a10 10 0 0 1 18.8-4.3M22 12.5a10 10 0 0 1-18.8 4.3" />
        </svg>
      </button>

      <label
        className="flex items-center gap-1 px-1.5 py-1 text-xs text-slate-500 select-none"
        title="Resume from docs_staging checkpoint when available"
      >
        <input
          type="checkbox"
          checked={resumeGenerate}
          onChange={(e) => setResumeGenerate(e.target.checked)}
          className="h-3 w-3 accent-accent-primary"
          onClick={(e) => e.stopPropagation()}
        />
        Resume
      </label>

      {/* Generate */}
      <button
        className="btn-icon-action"
        disabled={busy || generate.isPending}
        title="Generate Docs"
        onClick={() => {
          const writeModel = localStorage.getItem(`project_write_model_${projectId}`) || null;
          onAction(projectId);
          generate.mutate({
            projectId,
            resume: resumeGenerate,
            model: null,
            writeModel,
          });
        }}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
          <polyline points="14 2 14 8 20 8" />
          <line x1="16" y1="13" x2="8" y2="13" />
          <line x1="16" y1="17" x2="8" y2="17" />
        </svg>
      </button>

      {/* Push to RAG */}
      <button
        className="btn-ghost !px-2 !py-0.5 !text-xs"
        disabled={busy || push.isPending}
        title="Push to RAG"
        onClick={() => { onAction(projectId); push.mutate(projectId); }}
      >
        RAG
      </button>

      {/* Delete */}
      {!busy && (
        <button
          className="btn-icon-danger"
          disabled={deleteProject.isPending}
          title="Delete project"
          onClick={() => {
            if (window.confirm(`Permanently delete project '${projectId}'? This removes all scan data, docs, and RAG entries.`)) {
              deleteProject.mutate(projectId);
            }
          }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="3 6 5 6 21 6" />
            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
          </svg>
        </button>
      )}

      {/* Reset (only when busy) */}
      {busy && (
        <button
          className="btn-icon-danger"
          disabled={resetStatus.isPending}
          title="Reset status"
          onClick={() => resetStatus.mutate(projectId)}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      )}
    </div>
  );
}
