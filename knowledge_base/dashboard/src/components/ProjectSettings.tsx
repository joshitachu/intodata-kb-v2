import { useState, useEffect } from "react";
import { useUpdateProject, useProjectUsage } from "../hooks/useProjects";
import type { Project } from "../types";

interface Props {
  project: Project | null;
}

const PLATFORMS = ["talend", "workato", "lobster", "adf", "mulesoft", "dbt"];

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

export default function ProjectSettings({ project }: Props) {
  const updateProject = useUpdateProject();
  const usage = useProjectUsage(project?.id ?? null);
  const [displayName, setDisplayName] = useState("");
  const [platform, setPlatform] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (project) {
      setDisplayName(project.display_name);
      setPlatform(project.platform);
      setSaved(false);
    }
  }, [project?.id]);

  if (!project) return null;

  const hasChanges =
    displayName !== project.display_name || platform !== project.platform;

  function handleSave() {
    if (!project || !hasChanges) return;
    updateProject.mutate(
      {
        projectId: project.id,
        data: {
          display_name: displayName !== project.display_name ? displayName : undefined,
          platform: platform !== project.platform ? platform : undefined,
        },
      },
      {
        onSuccess: () => {
          setSaved(true);
          setTimeout(() => setSaved(false), 2000);
        },
      }
    );
  }

  return (
    <div className="bg-white rounded-lg shadow border">
      <div className="px-4 py-3 border-b bg-gray-50">
        <h3 className="text-sm font-semibold text-gray-700">Project Settings</h3>
      </div>

      <div className="p-4 space-y-4">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {/* Read-only info */}
          <div>
            <label className="block text-xs text-gray-500 mb-1">Project ID</label>
            <div className="text-sm font-mono bg-gray-50 rounded px-3 py-2 text-gray-600">
              {project.id}
            </div>
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Source Type</label>
            <div className="text-sm bg-gray-50 rounded px-3 py-2 text-gray-600">
              {project.source_type}
            </div>
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Local Path</label>
            <div className="text-sm font-mono bg-gray-50 rounded px-3 py-2 text-gray-600 truncate" title={project.local_path}>
              {project.local_path}
            </div>
          </div>
          {project.repo_url && (
            <div>
              <label className="block text-xs text-gray-500 mb-1">Repo URL</label>
              <div className="text-sm font-mono bg-gray-50 rounded px-3 py-2 text-gray-600 truncate" title={project.repo_url}>
                {project.repo_url}
              </div>
            </div>
          )}
        </div>

        <hr className="border-gray-200" />

        {/* Editable fields */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="block text-xs text-gray-500 mb-1">Display Name</label>
            <input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className="w-full border rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Platform</label>
            <select
              value={platform}
              onChange={(e) => setPlatform(e.target.value)}
              className="w-full border rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
            >
              {PLATFORMS.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
              {!PLATFORMS.includes(platform) && (
                <option value={platform}>{platform}</option>
              )}
            </select>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={handleSave}
            disabled={!hasChanges || updateProject.isPending}
            className={`px-4 py-2 text-sm rounded font-medium ${
              hasChanges
                ? "bg-blue-600 text-white hover:bg-blue-700"
                : "bg-gray-200 text-gray-400 cursor-not-allowed"
            }`}
          >
            {updateProject.isPending ? "Saving..." : "Save Changes"}
          </button>
          {saved && (
            <span className="text-sm text-green-600">Saved!</span>
          )}
          {updateProject.isError && (
            <span className="text-sm text-red-600">
              Failed: {updateProject.error?.message}
            </span>
          )}
        </div>

        {/* Usage Summary */}
        <hr className="border-gray-200" />
        <div>
          <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">Usage Summary</h4>
          {usage.isLoading ? (
            <p className="text-xs text-gray-400">Loading usage data...</p>
          ) : usage.isError ? (
            <p className="text-xs text-red-500">Failed to load usage data</p>
          ) : usage.data ? (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <div className="bg-gray-50 rounded px-3 py-2">
                <div className="text-lg font-semibold text-gray-800">
                  {usage.data.completed_generate_runs}/{usage.data.total_generate_runs}
                </div>
                <div className="text-xs text-gray-500">Generate Runs</div>
              </div>
              <div className="bg-gray-50 rounded px-3 py-2">
                <div className="text-lg font-semibold text-gray-800">
                  {usage.data.completed_scan_runs}/{usage.data.total_scan_runs}
                </div>
                <div className="text-xs text-gray-500">Scan Runs</div>
              </div>
              <div className="bg-gray-50 rounded px-3 py-2">
                <div className="text-lg font-semibold text-blue-600">
                  {formatTokens(usage.data.total_input_tokens + usage.data.total_output_tokens)}
                </div>
                <div className="text-xs text-gray-500">
                  Total Tokens
                  <span className="block text-gray-400">
                    {formatTokens(usage.data.total_input_tokens)} in / {formatTokens(usage.data.total_output_tokens)} out
                  </span>
                </div>
              </div>
              <div className="bg-gray-50 rounded px-3 py-2">
                <div className="text-lg font-semibold text-green-600">
                  ${usage.data.total_cost_usd.toFixed(2)}
                </div>
                <div className="text-xs text-gray-500">Total Cost</div>
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
