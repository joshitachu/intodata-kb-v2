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
    <div className="fixed inset-0 bg-black/60 backdrop-blur-md flex items-center justify-center z-50">
      <div className="bg-surface-card border border-white/[0.07] rounded shadow-2xl w-full max-w-md p-6 animate-fade-in">
        <h2 className="text-lg font-semibold text-slate-100 mb-4">Add Repository</h2>

        {/* Tabs */}
        <div className="flex gap-2 mb-4">
          <button
            className={`px-3 py-1.5 text-sm font-medium rounded transition-all ${
              tab === "path"
                ? "bg-accent-primary/10 text-accent-primary border border-accent-primary/40"
                : "border border-white/[0.07] text-slate-400 hover:text-slate-200"
            }`}
            onClick={() => setTab("path")}
          >
            URL / Path
          </button>
          <button
            className={`px-3 py-1.5 text-sm font-medium rounded transition-all ${
              tab === "zip"
                ? "bg-accent-primary/10 text-accent-primary border border-accent-primary/40"
                : "border border-white/[0.07] text-slate-400 hover:text-slate-200"
            }`}
            onClick={() => setTab("zip")}
          >
            Upload ZIP
          </button>
        </div>

        {/* Common fields */}
        <div className="space-y-3">
          <div>
            <label className="block text-sm font-medium text-slate-400 mb-1">Project ID</label>
            <input
              type="text"
              className="input-dark"
              placeholder="e.g. vaxxinova"
              value={projectId}
              onChange={(e) => setProjectId(e.target.value)}
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-slate-400 mb-1">Platform</label>
            <select
              className="input-dark"
              value={platform}
              onChange={(e) => setPlatform(e.target.value)}
            >
              <option value="talend">Talend</option>
              <option value="workato">Workato</option>
              <option value="lobster">Lobster</option>
              <option value="generic">Generic</option>
            </select>
          </div>

          {tab === "path" ? (
            <div>
              <label className="block text-sm font-medium text-slate-400 mb-1">
                Git URL or Local Path
              </label>
              <input
                type="text"
                className="input-dark"
                placeholder="https://github.com/... or /path/to/repo"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
              />
            </div>
          ) : (
            <div>
              <label className="block text-sm font-medium text-slate-400 mb-1">ZIP File</label>
              <input
                type="file"
                accept=".zip"
                className="w-full text-sm text-slate-400 file:mr-3 file:py-1.5 file:px-3 file:rounded file:border-0 file:text-sm file:font-medium file:bg-surface-raised file:text-slate-300 hover:file:bg-[#1e2d4a]"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
            </div>
          )}
        </div>

        {/* Error */}
        {(addRepo.isError || uploadZip.isError) && (
          <p className="text-red-400 text-sm mt-3 font-mono">
            {addRepo.error?.message || uploadZip.error?.message}
          </p>
        )}

        {/* Actions */}
        <div className="flex justify-end gap-2 mt-6">
          <button
            className="px-4 py-2 text-sm font-medium text-slate-400 border border-white/[0.1] rounded hover:text-slate-200 hover:border-white/[0.2] transition-all"
            onClick={() => { onClose(); reset(); }}
          >
            Cancel
          </button>
          <button
            className="px-4 py-2 text-sm font-medium bg-accent-primary text-surface-bg rounded hover:bg-accent-primary/90 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
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
