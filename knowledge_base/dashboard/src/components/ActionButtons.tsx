import { useState } from "react";
import { useScan, useGenerate, usePushToRag, useResetStatus, useDeleteProject } from "../hooks/useProjects";
import { useProfiles } from "../hooks/useTemplates";

interface Props {
  projectId: string;
  status: string;
  platform?: string;
  onAction: (projectId: string) => void;
}

export default function ActionButtons({ projectId, status, platform, onAction }: Props) {
  const scan = useScan();
  const generate = useGenerate();
  const push = usePushToRag();
  const resetStatus = useResetStatus();
  const deleteProject = useDeleteProject();
  const { data: profilesData } = useProfiles({ platform: platform || undefined });

  const [selectedProfileId, setSelectedProfileId] = useState<number | undefined>(undefined);

  const busy = status === "scanning" || status === "generating";
  const profiles = profilesData?.profiles ?? [];

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

      {/* Profile selector (compact) */}
      {profiles.length > 0 && (
        <select
          value={selectedProfileId ?? ""}
          onChange={(e) => setSelectedProfileId(e.target.value ? Number(e.target.value) : undefined)}
          className="bg-transparent border border-white/[0.08] rounded text-xs text-slate-500 px-1.5 py-1 max-w-[120px] focus:border-accent-primary/40 focus:outline-none"
          title="Template profile"
          onClick={(e) => e.stopPropagation()}
        >
          <option value="">Default</option>
          {profiles.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      )}

      {/* Generate */}
      <button
        className="btn-icon-action"
        disabled={busy || generate.isPending}
        title={selectedProfileId ? `Generate (Profile: ${profiles.find(p => p.id === selectedProfileId)?.name})` : "Generate Docs"}
        onClick={() => {
          onAction(projectId);
          generate.mutate({ projectId, templateProfileId: selectedProfileId });
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
