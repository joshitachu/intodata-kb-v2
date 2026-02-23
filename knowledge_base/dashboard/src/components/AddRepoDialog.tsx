import { useState } from "react";
import { useAddRepo, useUploadZip } from "../hooks/useProjects";

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function AddRepoDialog({ open, onClose }: Props) {
  const [tab, setTab] = useState<"path" | "zip">("path");
  const [url, setUrl] = useState("");
  const [projectId, setProjectId] = useState("");
  const [platform, setPlatform] = useState("talend");
  const [file, setFile] = useState<File | null>(null);

  const addRepo = useAddRepo();
  const uploadZip = useUploadZip();

  if (!open) return null;

  const handleSubmitPath = () => {
    if (!url || !projectId) return;
    addRepo.mutate(
      { url, project_id: projectId, platform },
      { onSuccess: () => { onClose(); reset(); } }
    );
  };

  const handleSubmitZip = () => {
    if (!file || !projectId) return;
    uploadZip.mutate(
      { file, projectId, platform },
      { onSuccess: () => { onClose(); reset(); } }
    );
  };

  const reset = () => {
    setUrl("");
    setProjectId("");
    setFile(null);
  };

  const busy = addRepo.isPending || uploadZip.isPending;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md p-6">
        <h2 className="text-lg font-semibold mb-4">Add Repository</h2>

        {/* Tabs */}
        <div className="flex gap-2 mb-4">
          <button
            className={`px-3 py-1 text-sm rounded ${tab === "path" ? "bg-blue-600 text-white" : "bg-gray-200"}`}
            onClick={() => setTab("path")}
          >
            URL / Path
          </button>
          <button
            className={`px-3 py-1 text-sm rounded ${tab === "zip" ? "bg-blue-600 text-white" : "bg-gray-200"}`}
            onClick={() => setTab("zip")}
          >
            Upload ZIP
          </button>
        </div>

        {/* Common fields */}
        <div className="space-y-3">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Project ID</label>
            <input
              type="text"
              className="w-full border rounded px-3 py-1.5 text-sm"
              placeholder="e.g. vaxxinova"
              value={projectId}
              onChange={(e) => setProjectId(e.target.value)}
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Platform</label>
            <select
              className="w-full border rounded px-3 py-1.5 text-sm"
              value={platform}
              onChange={(e) => setPlatform(e.target.value)}
            >
              <option value="talend">Talend</option>
            </select>
          </div>

          {tab === "path" ? (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Git URL or Local Path
              </label>
              <input
                type="text"
                className="w-full border rounded px-3 py-1.5 text-sm"
                placeholder="https://github.com/... or C:/path/to/repo"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
              />
            </div>
          ) : (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">ZIP File</label>
              <input
                type="file"
                accept=".zip"
                className="w-full text-sm"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
            </div>
          )}
        </div>

        {/* Error */}
        {(addRepo.isError || uploadZip.isError) && (
          <p className="text-red-600 text-sm mt-2">
            {addRepo.error?.message || uploadZip.error?.message}
          </p>
        )}

        {/* Actions */}
        <div className="flex justify-end gap-2 mt-6">
          <button
            className="px-4 py-1.5 text-sm border rounded hover:bg-gray-50"
            onClick={() => { onClose(); reset(); }}
          >
            Cancel
          </button>
          <button
            className="px-4 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
            disabled={busy || !projectId || (tab === "path" ? !url : !file)}
            onClick={tab === "path" ? handleSubmitPath : handleSubmitZip}
          >
            {busy ? "Adding..." : "Add"}
          </button>
        </div>
      </div>
    </div>
  );
}
