import { useState, useRef } from "react";
import {
  usePluginPacks,
  useRegisterPluginPack,
  useUploadPluginPackZip,
  usePullPluginPack,
  useDeletePluginPack,
  usePluginPackFiles,
  usePluginPackFile,
  useSavePluginPackFile,
} from "../hooks/useProjects";
import type { PluginPack } from "../types";

type AddTab = "upload" | "github";

export default function PluginPacksPage() {
  const { data: packs = [], isLoading } = usePluginPacks();
  const [selectedPackId, setSelectedPackId] = useState<number | null>(null);
  const [showAdd, setShowAdd] = useState(false);

  const selectedPack = (packs as PluginPack[]).find((p) => p.id === selectedPackId) ?? null;

  return (
    <div className="grid grid-cols-12 gap-6">
      {/* Left: pack list */}
      <div className="col-span-4 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-mono text-slate-400">
            Plugin Packs
            {(packs as PluginPack[]).length > 0 && (
              <span className="ml-2 text-xs text-slate-600">({(packs as PluginPack[]).length})</span>
            )}
          </h3>
          <button className="btn-ghost !text-xs" onClick={() => setShowAdd(true)}>
            + Add Pack
          </button>
        </div>

        {isLoading && (
          <p className="text-xs text-slate-500 font-mono">Loading...</p>
        )}

        <div className="space-y-1">
          {(packs as PluginPack[]).map((pack) => (
            <button
              key={pack.id}
              className={`w-full text-left px-3 py-2.5 rounded text-sm transition-all ${
                selectedPackId === pack.id
                  ? "bg-white/[0.08] text-slate-200"
                  : "text-slate-400 hover:bg-white/[0.04] hover:text-slate-300"
              }`}
              onClick={() => setSelectedPackId(pack.id)}
            >
              <div className="font-medium">{pack.name}</div>
              <div className="text-xs text-slate-600 mt-0.5">v{pack.version}</div>
            </button>
          ))}

          {!isLoading && (packs as PluginPack[]).length === 0 && (
            <p className="text-xs text-slate-600 font-mono px-1">No packs registered.</p>
          )}
        </div>
      </div>

      {/* Right: pack editor */}
      <div className="col-span-8">
        {selectedPack ? (
          <PackEditor pack={selectedPack} onDeleted={() => setSelectedPackId(null)} />
        ) : (
          <div className="card-dark p-12 text-center text-slate-500 text-sm">
            Select a plugin pack to view and edit its files.
          </div>
        )}
      </div>

      {showAdd && <AddPackModal onClose={() => setShowAdd(false)} />}
    </div>
  );
}


// ---------------------------------------------------------------------------
// Pack editor: header + file list + textarea
// ---------------------------------------------------------------------------

function PackEditor({ pack, onDeleted }: { pack: PluginPack; onDeleted: () => void }) {
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const pullMut = usePullPluginPack();
  const deleteMut = useDeletePluginPack();

  const { data: files = [] } = usePluginPackFiles(pack.id);

  async function handleDelete() {
    if (!confirm(`Delete pack "${pack.name}"? Projects using it will be unassigned.`)) return;
    await deleteMut.mutateAsync(pack.id);
    onDeleted();
  }

  async function handlePull() {
    await pullMut.mutateAsync(pack.id);
  }

  return (
    <div className="space-y-4">
      {/* Pack header */}
      <div className="card-dark px-4 py-3 flex items-start justify-between gap-4">
        <div>
          <div className="text-sm font-medium text-slate-200">{pack.name}</div>
          <div className="text-xs text-slate-500 mt-0.5">v{pack.version}</div>
          {pack.repo_url && (
            <div className="text-xs text-slate-600 mt-1 font-mono break-all">{pack.repo_url}</div>
          )}
          {pack.description && (
            <div className="text-xs text-slate-500 mt-1">{pack.description}</div>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {pack.repo_url && (
            <button
              className="btn-ghost !text-xs"
              onClick={handlePull}
              disabled={pullMut.isPending}
            >
              {pullMut.isPending ? "Pulling..." : "Pull latest"}
            </button>
          )}
          <button
            className="px-2.5 py-1 text-xs text-red-400 border border-red-500/30 rounded hover:border-red-500/60 transition-all"
            onClick={handleDelete}
            disabled={deleteMut.isPending}
          >
            Delete
          </button>
        </div>
      </div>

      {/* Two-column: file list + editor */}
      <div className="grid grid-cols-12 gap-4">
        <div className="col-span-4">
          <div className="card-dark p-3 space-y-0.5">
            <div className="text-xs font-mono text-slate-600 mb-2 uppercase tracking-wider">Files</div>
            {files.map((f) => (
              <button
                key={f}
                className={`w-full text-left px-2 py-1.5 rounded text-xs font-mono transition-all ${
                  selectedFile === f
                    ? "bg-white/[0.08] text-slate-200"
                    : "text-slate-500 hover:bg-white/[0.04] hover:text-slate-300"
                }`}
                onClick={() => setSelectedFile(f)}
              >
                {f}
              </button>
            ))}
            {files.length === 0 && (
              <p className="text-xs text-slate-600 px-2">No editable files found.</p>
            )}
          </div>
        </div>

        <div className="col-span-8">
          {selectedFile ? (
            <FileEditor packId={pack.id} filePath={selectedFile} />
          ) : (
            <div className="card-dark p-8 text-center text-slate-600 text-xs font-mono">
              Select a file to edit
            </div>
          )}
        </div>
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// File editor
// ---------------------------------------------------------------------------

function FileEditor({ packId, filePath }: { packId: number; filePath: string }) {
  const { data, isLoading } = usePluginPackFile(packId, filePath);
  const saveMut = useSavePluginPackFile();
  const [draft, setDraft] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  const content = draft ?? data?.content ?? "";
  const isDirty = draft !== null && draft !== data?.content;

  // Reset draft when file changes
  const lastFilePath = useRef(filePath);
  if (lastFilePath.current !== filePath) {
    lastFilePath.current = filePath;
    setDraft(null);
    setSaveError(null);
  }

  async function handleSave() {
    setSaveError(null);
    try {
      await saveMut.mutateAsync({ packId, filePath, content });
      setDraft(null);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Save failed");
    }
  }

  if (isLoading) {
    return <div className="card-dark p-8 text-center text-slate-600 text-xs font-mono">Loading...</div>;
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-mono text-slate-500">{filePath}</span>
        <button
          className="btn-primary !text-xs !py-1 !px-3"
          onClick={handleSave}
          disabled={!isDirty || saveMut.isPending}
        >
          {saveMut.isPending ? "Saving..." : "Save"}
        </button>
      </div>
      <textarea
        className="w-full h-96 bg-[#0d1117] border border-white/[0.08] rounded p-3 text-xs font-mono text-slate-300 resize-y focus:outline-none focus:border-indigo-500/50 transition-colors"
        value={content}
        onChange={(e) => setDraft(e.target.value)}
        spellCheck={false}
      />
      {saveError && (
        <p className="text-xs text-red-400 font-mono">{saveError}</p>
      )}
    </div>
  );
}


// ---------------------------------------------------------------------------
// Add Pack modal
// ---------------------------------------------------------------------------

function AddPackModal({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<AddTab>("upload");
  const [repoUrl, setRepoUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const uploadMut = useUploadPluginPackZip();
  const registerMut = useRegisterPluginPack();

  async function handleUpload() {
    const file = fileInputRef.current?.files?.[0];
    if (!file) return;
    setError(null);
    try {
      await uploadMut.mutateAsync(file);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    }
  }

  async function handleRegister() {
    if (!repoUrl.trim()) return;
    setError(null);
    try {
      await registerMut.mutateAsync({ repo_url: repoUrl.trim() });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registration failed");
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-surface-card border border-white/[0.08] rounded-lg shadow-xl w-full max-w-md p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium text-slate-200">Add Plugin Pack</h3>
          <button className="text-slate-500 hover:text-slate-300 text-xs" onClick={onClose}>
            Close
          </button>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 border-b border-white/[0.06] pb-0">
          {(["upload", "github"] as AddTab[]).map((t) => (
            <button
              key={t}
              className={`px-3 py-1.5 text-xs rounded-t transition-all ${
                tab === t ? "text-slate-200 bg-white/[0.06]" : "text-slate-500 hover:text-slate-300"
              }`}
              onClick={() => { setTab(t); setError(null); }}
            >
              {t === "upload" ? "Upload ZIP" : "GitHub URL"}
            </button>
          ))}
        </div>

        {tab === "upload" && (
          <div className="space-y-3">
            <p className="text-xs text-slate-500">
              Upload a <code className="font-mono">.zip</code> containing{" "}
              <code className="font-mono">plugin.yaml</code> and optional{" "}
              <code className="font-mono">prompts/</code> and{" "}
              <code className="font-mono">examples/</code> folders. Max 10 MB.
            </p>
            <PackStructureHint />
            <input
              ref={fileInputRef}
              type="file"
              accept=".zip"
              className="text-xs text-slate-400 file:btn-ghost file:mr-3 file:text-xs"
            />
            <button
              className="btn-primary w-full !text-xs"
              onClick={handleUpload}
              disabled={uploadMut.isPending}
            >
              {uploadMut.isPending ? "Uploading..." : "Upload & Register"}
            </button>
          </div>
        )}

        {tab === "github" && (
          <div className="space-y-3">
            <p className="text-xs text-slate-500">
              Provide a GitHub repository URL. The server clones the repository and validates{" "}
              <code className="font-mono">plugin.yaml</code> at the repository root.
            </p>
            <PackStructureHint />
            <input
              type="text"
              className="input-dark w-full text-xs font-mono"
              placeholder="https://github.com/org/my-rulebook"
              value={repoUrl}
              onChange={(e) => setRepoUrl(e.target.value)}
            />
            <button
              className="btn-primary w-full !text-xs"
              onClick={handleRegister}
              disabled={registerMut.isPending || !repoUrl.trim()}
            >
              {registerMut.isPending ? "Cloning..." : "Clone & Register"}
            </button>
          </div>
        )}

        {error && (
          <p className="text-xs text-red-400 font-mono break-all">{error}</p>
        )}
      </div>
    </div>
  );
}

function PackStructureHint() {
  return (
    <div className="rounded border border-white/[0.08] bg-black/20 p-3">
      <div className="text-[11px] font-mono text-slate-500 mb-2">Expected pack root</div>
      <pre className="text-[11px] leading-5 font-mono text-slate-400 whitespace-pre-wrap">{`plugin.yaml
prompts/
  phase2.md
  phase3.md
  scorecard.md
  docs/
    YOUR_DOC_NAME.md`}</pre>
      <p className="text-[11px] text-slate-500 mt-2">
        Document prompt names should match the <code className="font-mono">docs.name</code> entries in{" "}
        <code className="font-mono">plugin.yaml</code>.
      </p>
    </div>
  );
}
