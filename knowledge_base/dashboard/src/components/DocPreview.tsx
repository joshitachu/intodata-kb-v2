import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
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

  if (isLoading) return <div className="p-4 text-sm text-gray-400">Loading...</div>;
  if (error) return <div className="p-4 text-sm text-red-500">Failed to load document.</div>;

  return (
    <div className="p-4 overflow-y-auto max-h-[500px]">
      <pre className="text-sm text-gray-700 whitespace-pre-wrap font-mono leading-relaxed">
        {data?.content}
      </pre>
    </div>
  );
}

export default function DocPreview({ projectId }: Props) {
  const { data, isLoading } = useDocs(projectId);
  const [selectedDoc, setSelectedDoc] = useState<string | null>(null);

  if (!projectId) return null;

  const docs: DocFile[] = data?.docs ?? [];

  return (
    <div className="bg-white rounded-lg shadow border">
      <div className="px-4 py-3 border-b bg-gray-50">
        <h3 className="text-sm font-semibold text-gray-700">
          Documentation
          {docs.length > 0 && (
            <span className="ml-2 text-xs font-normal text-gray-400">
              ({docs.length} file{docs.length !== 1 ? "s" : ""})
            </span>
          )}
        </h3>
      </div>

      {isLoading ? (
        <div className="p-4 text-sm text-gray-400">Loading...</div>
      ) : docs.length === 0 ? (
        <div className="p-4 text-sm text-gray-400 italic">
          No documentation files yet. Generate docs first.
        </div>
      ) : (
        <div className="flex flex-col lg:flex-row">
          {/* File list */}
          <div className="lg:w-64 border-b lg:border-b-0 lg:border-r">
            <ul className="divide-y">
              {docs.map((doc) => (
                <li key={doc.name}>
                  <button
                    className={`w-full text-left px-4 py-2 text-sm hover:bg-gray-50 ${
                      selectedDoc === doc.name ? "bg-blue-50 text-blue-700" : "text-gray-700"
                    }`}
                    onClick={() => setSelectedDoc(selectedDoc === doc.name ? null : doc.name)}
                  >
                    <div className="font-medium truncate">{doc.name}</div>
                    <div className="text-xs text-gray-400">{formatSize(doc.size_bytes)}</div>
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
              <div className="p-4 text-sm text-gray-400 italic">
                Select a document to preview.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
