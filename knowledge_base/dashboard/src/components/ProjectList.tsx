import { useProjects } from "../hooks/useProjects";
import type { Project } from "../types";
import ActionButtons from "./ActionButtons";
import StatusBadge from "./StatusBadge";

interface Props {
  onAction: (projectId: string) => void;
  activeProject?: string | null;
  onSelect: (projectId: string) => void;
  canManageProjects?: boolean;
}

function formatDate(d: string | null): string {
  if (!d) return "-";
  return new Date(d).toLocaleString();
}

export default function ProjectList({ onAction, activeProject, onSelect, canManageProjects = true }: Props) {
  const { data, isLoading, error } = useProjects();

  if (isLoading) {
    return <p className="text-gray-500 text-sm p-4">Loading projects...</p>;
  }

  if (error) {
    return (
      <p className="text-red-600 text-sm p-4">
        Failed to load projects: {(error as Error).message}
      </p>
    );
  }

  const projects: Project[] = data?.projects ?? [];

  if (projects.length === 0) {
    return (
      <p className="text-gray-500 text-sm p-4 italic">
        No projects registered. Click "Add Repo" to get started.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b bg-gray-50 text-left text-gray-600">
            <th className="px-4 py-2 font-medium">Project</th>
            <th className="px-4 py-2 font-medium">Platform</th>
            <th className="px-4 py-2 font-medium">Status</th>
            <th className="px-4 py-2 font-medium">Jobs</th>
            <th className="px-4 py-2 font-medium">Docs</th>
            <th className="px-4 py-2 font-medium">Last Scan</th>
            {canManageProjects && <th className="px-4 py-2 font-medium">Actions</th>}
          </tr>
        </thead>
        <tbody>
          {projects.map((p) => (
            <tr
              key={p.id}
              className={`border-b hover:bg-gray-50 ${activeProject === p.id ? "bg-blue-50" : ""}`}
            >
              <td className="px-4 py-3">
                <button
                  className="text-left group"
                  onClick={() => onSelect(p.id)}
                >
                  <div className="font-medium text-blue-600 group-hover:underline">
                    {p.display_name}
                  </div>
                  <div className="text-xs text-gray-400">{p.id}</div>
                </button>
              </td>
              <td className="px-4 py-3">{p.platform}</td>
              <td className="px-4 py-3">
                <StatusBadge status={p.status} />
                {p.error_message && (
                  <div className="text-xs text-red-500 mt-1 max-w-xs truncate" title={p.error_message}>
                    {p.error_message}
                  </div>
                )}
              </td>
              <td className="px-4 py-3">{p.job_count}</td>
              <td className="px-4 py-3">{p.doc_count}</td>
              <td className="px-4 py-3 text-xs text-gray-500">
                {formatDate(p.last_scan_at)}
              </td>
              {canManageProjects && (
                <td className="px-4 py-3">
                  <ActionButtons
                    projectId={p.id}
                    status={p.status}
                    onAction={onAction}
                  />
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
