import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { useDocs } from "../hooks/useProjects";
import { api } from "../api/client";
import type { DocFile } from "../types";

interface Props {
  projectId: string | null;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function DocContentPanel({ projectId, filename }: { projectId: string; filename: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["doc-content", projectId, filename],
    queryFn: () => api.getDocContent(projectId, filename),
  });

  if (isLoading) return <div className="p-6 text-sm text-slate-500">Loading...</div>;
  if (error) return <div className="p-6 text-sm text-red-400">Failed to load document.</div>;

  return (
    <div className="p-4 overflow-y-auto max-h-[500px]">
      <pre className="text-sm text-slate-400 whitespace-pre-wrap font-mono leading-relaxed">
        {data?.content}
      </pre>
    </div>
  );
}

export default function DocPreview({ projectId }: Props) {
  const { data, isLoading } = useDocs(projectId);
  const [selectedDoc, setSelectedDoc] = useState<string | null>(null);

  const generateAssessment = useMutation({
    mutationFn: () => api.generateAssessment(projectId!),
    onSuccess: () => {
      window.open(`/mcp/projects/${projectId}/assessment`, "_blank");
    },
  });

  if (!projectId) return null;

  const docs: DocFile[] = data?.docs ?? [];
  const hasDocs = docs.length > 0;

  return (
    <div className="card-dark">
      <div className="px-4 py-3 border-b border-white/[0.05] bg-surface-raised rounded-t flex items-center justify-between">
        <h3 className="text-sm font-mono text-slate-400">
          Documentation
          {docs.length > 0 && (
            <span className="ml-2 text-xs text-slate-600">
              ({docs.length} file{docs.length !== 1 ? "s" : ""})
            </span>
          )}
        </h3>
        {hasDocs && (
          <button
            className="btn-ghost"
            disabled={generateAssessment.isPending}
            onClick={() => generateAssessment.mutate()}
          >
            {generateAssessment.isPending ? "Generating..." : "View Assessment"}
          </button>
        )}
      </div>

      {isLoading ? (
        <div className="p-6 text-sm text-slate-500">Loading...</div>
      ) : docs.length === 0 ? (
        <div className="p-6 text-sm text-slate-500 font-mono">
          No documentation files yet. Generate docs first.
        </div>
      ) : (
        <div className="flex flex-col lg:flex-row">
          {/* File list */}
          <div className="lg:w-64 border-b lg:border-b-0 lg:border-r border-white/[0.05]">
            <ul className="divide-y divide-white/[0.04]">
              {docs.map((doc) => (
                <li key={doc.name}>
                  <button
                    className={`w-full text-left px-4 py-2.5 text-sm transition-colors ${
                      selectedDoc === doc.name
                        ? "bg-accent-primary/10 text-accent-primary"
                        : "text-slate-400 hover:bg-white/[0.02]"
                    }`}
                    onClick={() => setSelectedDoc(selectedDoc === doc.name ? null : doc.name)}
                  >
                    <div className="font-medium truncate">{doc.name}</div>
                    <div className="text-xs text-slate-600">{formatSize(doc.size_bytes)}</div>
                  </button>
                </li>
              ))}
            </ul>
          </div>

          {/* Content preview */}
          <div className="flex-1 min-h-[200px]">
            {selectedDoc ? (
              <DocContentPanel projectId={projectId} filename={selectedDoc} />
            ) : (
              <div className="p-6 text-sm text-slate-500 font-mono">
                Select a document to preview.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
