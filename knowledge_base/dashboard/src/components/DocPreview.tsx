import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { useDocs } from "../hooks/useProjects";
import { api } from "../api/client";
import { parseApiError } from "../lib/errors";
import type { DocFile, DocSource } from "../types";

interface Props {
  projectId: string | null;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** A document's identity within a project. `name` alone is ambiguous. */
interface DocRef {
  name: string;
  subproject: string | null;
}

function docRefKey(ref: DocRef): string {
  return `${ref.subproject ?? ""}/${ref.name}`;
}

function DocContentPanel({
  projectId,
  doc,
  source,
}: {
  projectId: string;
  doc: DocRef;
  source: DocSource;
}) {
  const { data, isLoading, error } = useQuery({
    // The sub-project is part of the cache key for the same reason it is part
    // of the request: two partitions publish the same filename with different
    // bytes, and keying on the name alone serves one partition's document
    // under another's label.
    queryKey: ["doc-content", projectId, doc.subproject, doc.name, source],
    queryFn: () => api.getDocContent(projectId, doc.name, source, doc.subproject),
    // Bytes on disk do not change while an operator reads them, and a 404 for
    // a specific document is a permanent answer (the global retry policy in
    // `main.tsx` already declines to retry a 4xx). Nothing here should be
    // re-requested without an explicit reason.
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  });

  if (isLoading) return <div className="p-6 text-sm text-slate-500">Loading...</div>;
  if (error) return <div className="p-6 text-sm text-red-400">{parseApiError(error)}</div>;

  return (
    <div className="p-4 overflow-y-auto max-h-[500px]">
      <pre className="text-sm text-slate-400 whitespace-pre-wrap font-mono leading-relaxed">
        {data?.content}
      </pre>
    </div>
  );
}

export default function DocPreview({ projectId }: Props) {
  const [source, setSource] = useState<DocSource>("published");
  const { data, isLoading, isError } = useDocs(projectId, source);
  const { data: stagingData } = useDocs(projectId, "staging", source === "staging");
  const [selectedDoc, setSelectedDoc] = useState<DocRef | null>(null);

  const generateAssessment = useMutation({
    mutationFn: () => api.generateAssessment(projectId!),
    onSuccess: () => {
      window.open(`/mcp/projects/${projectId}/assessment`, "_blank");
    },
  });

  if (!projectId) return null;

  const docs: DocFile[] = data?.docs ?? [];
  const hasDocs = docs.length > 0;
  const stagingCount = stagingData?.docs?.length ?? 0;
  // Non-empty only for a partitioned project. The server owns the display
  // order; a sub-project that somehow appears on a doc without appearing here
  // is still rendered (under its own heading) rather than dropped.
  const orderedSubprojects = Array.from(
    new Set([
      ...(data?.subprojects ?? []),
      ...docs.map((doc) => doc.subproject).filter((name): name is string => !!name),
    ]),
  );
  const groups: { subproject: string | null; docs: DocFile[] }[] =
    orderedSubprojects.length > 0
      ? orderedSubprojects.map((name) => ({
          subproject: name as string | null,
          docs: docs.filter((doc) => doc.subproject === name),
        }))
      : [{ subproject: null, docs }];

  return (
    <div className="card-dark">
      <div className="px-4 py-3 border-b border-white/[0.05] bg-surface-raised rounded-t flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h3 className="text-sm font-mono text-slate-400">
            Documentation
            {docs.length > 0 && (
              <span className="ml-2 text-xs text-slate-600">
                ({docs.length} file{docs.length !== 1 ? "s" : ""})
              </span>
            )}
          </h3>
          <div className="flex overflow-hidden rounded border border-white/[0.08]">
            {(["published", "staging"] as DocSource[]).map((option) => (
              <button
                key={option}
                className={`px-2.5 py-1 text-xs font-mono transition-colors ${
                  source === option
                    ? "bg-accent-primary/15 text-accent-primary"
                    : "text-slate-500 hover:bg-white/[0.03]"
                }`}
                onClick={() => {
                  setSource(option);
                  setSelectedDoc(null);
                }}
              >
                {option === "published" ? "Published" : `Staging${stagingCount ? ` ${stagingCount}` : ""}`}
              </button>
            ))}
          </div>
        </div>
        {hasDocs && source === "published" && (
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
      ) : isError ? (
        <div className="p-6 text-sm text-red-400 font-mono">Failed to load documentation.</div>
      ) : docs.length === 0 ? (
        <div className="p-6 text-sm text-slate-500 font-mono">
          {source === "staging"
            ? "No staged documentation files. Failed or in-progress enterprise output will appear here."
            : "No published documentation files yet. Generate docs first."}
        </div>
      ) : (
        <div className="flex flex-col lg:flex-row">
          {/* File list, grouped by sub-project for a partitioned project */}
          <div className="lg:w-64 border-b lg:border-b-0 lg:border-r border-white/[0.05]">
            {groups.map((group) => (
              <div key={group.subproject ?? "__flat__"}>
                {group.subproject && (
                  <div className="px-4 py-1.5 bg-white/[0.02] border-b border-white/[0.05] text-xs font-mono text-slate-500 truncate">
                    {group.subproject}
                  </div>
                )}
                <ul className="divide-y divide-white/[0.04]">
                  {group.docs.map((doc) => {
                    const ref: DocRef = { name: doc.name, subproject: doc.subproject };
                    const isSelected =
                      selectedDoc !== null && docRefKey(selectedDoc) === docRefKey(ref);
                    return (
                      <li key={docRefKey(ref)}>
                        <button
                          className={`w-full text-left px-4 py-2.5 text-sm transition-colors ${
                            isSelected
                              ? "bg-accent-primary/10 text-accent-primary"
                              : "text-slate-400 hover:bg-white/[0.02]"
                          }`}
                          onClick={() => setSelectedDoc(isSelected ? null : ref)}
                        >
                          <div className="font-medium truncate">{doc.name}</div>
                          <div className="text-xs text-slate-600">{formatSize(doc.size_bytes)}</div>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
          </div>

          {/* Content preview */}
          <div className="flex-1 min-h-[200px]">
            {selectedDoc ? (
              <DocContentPanel projectId={projectId} doc={selectedDoc} source={source} />
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
