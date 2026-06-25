import { useEffect, useState, useRef } from "react";
import {
  useDiscoverPluginPackBranches,
  usePluginPackBranchFile,
  usePluginPackBranchFiles,
  usePluginPackBranches,
  usePluginPacks,
  useStarterPack,
  useRegisterPluginPack,
  usePullPluginPack,
  useDeletePluginPack,
  usePluginPackFiles,
  usePluginPackFile,
  useSavePluginPackFile,
} from "../hooks/useProjects";
import type { PluginPack, PluginPackBranch, StarterPack } from "../types";

export default function PluginPacksPage() {
  const { data: packs = [], isLoading } = usePluginPacks();
  const { data: starter } = useStarterPack();
  const starterRepoUrl = starter?.repo_url ?? null;
  const {
    data: starterBranches,
    isLoading: isLoadingBranches,
    isError: isBranchError,
  } = usePluginPackBranches(starterRepoUrl);
  const [selectedPackId, setSelectedPackId] = useState<number | null>(null);
  const [selectedBranchName, setSelectedBranchName] = useState<string | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [starterCopyStatus, setStarterCopyStatus] = useState<string | null>(null);

  const selectedPack = (packs as PluginPack[]).find((p) => p.id === selectedPackId) ?? null;
  const availableBranches = (starterBranches?.branches ?? []).filter(
    (branch) => branch.has_pack && branch.valid && !branch.registered_pack_id,
  );
  const selectedBranch =
    selectedBranchName !== null
      ? availableBranches.find((branch) => branch.branch === selectedBranchName) ?? null
      : null;
  const totalVisiblePacks = (packs as PluginPack[]).length + availableBranches.length;

  return (
    <div className="grid grid-cols-12 gap-6">
      {/* Left: pack list */}
      <div className="col-span-4 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-mono text-slate-400">
            Swap Packs
            {totalVisiblePacks > 0 && (
              <span className="ml-2 text-xs text-slate-600">({totalVisiblePacks})</span>
            )}
          </h3>
          <div className="flex items-center gap-2">
            {starter?.repo_url && (
              <button className="btn-ghost !text-xs" onClick={() => openStarterRepo(starter)}>
                Public repo
              </button>
            )}
            {starter?.clone_command && (
              <button
                className="btn-ghost !text-xs"
                onClick={() => copyStarterClone(starter, setStarterCopyStatus)}
              >
                {starterCopyStatus ?? "Copy clone"}
              </button>
            )}
            <button className="btn-ghost !text-xs" onClick={() => setShowAdd(true)}>
              + Add Swap
            </button>
          </div>
        </div>

        {(isLoading || isLoadingBranches) && (
          <p className="text-xs text-slate-500 font-mono">Loading...</p>
        )}

        {isBranchError && (
          <p className="text-xs text-amber-400 font-mono px-1">
            Could not load GitHub branches.
          </p>
        )}

        <div className="space-y-1">
          {(packs as PluginPack[]).map((pack) => (
            <button
              key={pack.id}
              className={`w-full text-left px-3 py-2.5 rounded text-sm transition-all ${
                selectedPackId === pack.id && selectedBranchName === null
                  ? "bg-white/[0.08] text-slate-200"
                  : "text-slate-400 hover:bg-white/[0.04] hover:text-slate-300"
              }`}
              onClick={() => {
                setSelectedPackId(pack.id);
                setSelectedBranchName(null);
              }}
            >
              <div className="font-medium">{pack.repo_branch ?? pack.manifest_name ?? pack.name}</div>
              <div className="text-xs text-slate-600 mt-0.5">
                v{pack.version}
                {pack.repo_branch ? (
                  <span className="ml-2">{pack.manifest_name ?? pack.name}</span>
                ) : null}
              </div>
            </button>
          ))}

          {availableBranches.map((branch) => (
            <button
              key={`branch:${branch.branch}`}
              className={`w-full text-left px-3 py-2.5 rounded text-sm transition-all ${
                selectedBranchName === branch.branch
                  ? "bg-white/[0.08] text-slate-200"
                  : "text-slate-400 hover:bg-white/[0.04] hover:text-slate-300"
              }`}
              onClick={() => {
                setSelectedBranchName(branch.branch);
                setSelectedPackId(null);
              }}
            >
              <div className="font-medium">{branch.branch}</div>
              <div className="text-xs text-slate-600 mt-0.5">
                v{branch.version ?? "unknown"}
                {branch.name ? <span className="ml-2">{branch.name}</span> : null}
                <span className="ml-2 text-emerald-500/80">available</span>
              </div>
            </button>
          ))}

          {!isLoading && !isLoadingBranches && totalVisiblePacks === 0 && (
            <p className="text-xs text-slate-600 font-mono px-1">No swap packs found.</p>
          )}
        </div>
      </div>

      {/* Right: pack editor */}
      <div className="col-span-8">
        {selectedPack ? (
          <PackEditor pack={selectedPack} onDeleted={() => setSelectedPackId(null)} />
        ) : selectedBranch && starterRepoUrl ? (
          <AvailableBranchPanel
            branch={selectedBranch}
            repoUrl={starterRepoUrl}
            onRegistered={(pack) => {
              setSelectedBranchName(null);
              setSelectedPackId(pack.id);
            }}
          />
        ) : (
          <div className="card-dark p-12 text-center text-slate-500 text-sm">
            Select a swap pack to view and edit its files.
          </div>
        )}
      </div>

      {showAdd && <AddPackModal starter={starter ?? null} onClose={() => setShowAdd(false)} />}
    </div>
  );
}

function openStarterRepo(starter: StarterPack) {
  if (!starter.repo_url) return;
  window.open(starter.repo_url, "_blank", "noopener,noreferrer");
}

async function copyStarterClone(
  starter: StarterPack,
  setStatus: (value: string | null) => void,
) {
  if (!starter.clone_command) return;
  setStatus(null);
  try {
    await navigator.clipboard.writeText(starter.clone_command);
    setStatus("Copied");
  } catch {
    setStatus("Copy failed");
  }
}


function AvailableBranchPanel({
  branch,
  repoUrl,
  onRegistered,
}: {
  branch: PluginPackBranch;
  repoUrl: string;
  onRegistered: (pack: PluginPack) => void;
}) {
  const registerMut = useRegisterPluginPack();
  const [copyStatus, setCopyStatus] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const {
    data: files = [],
    isLoading: isLoadingFiles,
    isError: isFileListError,
  } = usePluginPackBranchFiles(repoUrl, branch.branch);

  useEffect(() => {
    const preferred = files.includes("plugin.yaml") ? "plugin.yaml" : (files[0] ?? null);
    if (preferred === null) {
      setSelectedFile(null);
      return;
    }
    if (selectedFile === null || !files.includes(selectedFile)) {
      setSelectedFile(preferred);
    }
  }, [branch.branch, files, selectedFile]);

  async function handleRegister() {
    const pack = await registerMut.mutateAsync({ repo_url: repoUrl, repo_branch: branch.branch });
    onRegistered(pack);
  }

  async function handleCopyCloneCommand() {
    setCopyStatus(null);
    try {
      await navigator.clipboard.writeText(`git clone --branch ${branch.branch} ${repoUrl}`);
      setCopyStatus("Copied");
    } catch {
      setCopyStatus("Copy failed");
    }
  }

  return (
    <div className="space-y-4">
      <div className="card-dark px-4 py-3 flex items-start justify-between gap-4">
        <div>
          <div className="text-sm font-medium text-slate-200">{branch.branch}</div>
          <div className="text-xs text-slate-500 mt-0.5">v{branch.version ?? "unknown"}</div>
          {branch.name && (
            <div className="text-xs text-slate-600 mt-1">manifest {branch.name}</div>
          )}
          <div className="text-xs text-slate-600 mt-1 font-mono break-all">{repoUrl}</div>
          {branch.commit_sha && (
            <div className="text-xs text-slate-600 mt-1 font-mono">
              commit {branch.commit_sha.slice(0, 12)}
            </div>
          )}
          {branch.description && (
            <div className="text-xs text-slate-500 mt-1">{branch.description}</div>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button className="btn-ghost !text-xs" onClick={handleCopyCloneCommand}>
            {copyStatus ?? "Copy clone"}
          </button>
          <button
            className="btn-primary !text-xs"
            onClick={handleRegister}
            disabled={registerMut.isPending}
          >
            {registerMut.isPending ? "Registering..." : "Register branch"}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-12 gap-4">
        <div className="col-span-4">
          <div className="card-dark p-3 space-y-0.5">
            <div className="text-xs font-mono text-slate-600 mb-2 uppercase tracking-wider">
              Files
            </div>
            {isLoadingFiles && (
              <p className="text-xs text-slate-600 px-2">Loading...</p>
            )}
            {isFileListError && (
              <p className="text-xs text-amber-400 px-2">Could not load files.</p>
            )}
            {files.map((file) => (
              <button
                key={file}
                className={`w-full text-left px-2 py-1.5 rounded text-xs font-mono transition-all ${
                  selectedFile === file
                    ? "bg-white/[0.08] text-slate-200"
                    : "text-slate-500 hover:bg-white/[0.04] hover:text-slate-300"
                }`}
                onClick={() => setSelectedFile(file)}
              >
                {file}
              </button>
            ))}
            {!isLoadingFiles && !isFileListError && files.length === 0 && (
              <p className="text-xs text-slate-600 px-2">No editable files found.</p>
            )}
          </div>
        </div>

        <div className="col-span-8">
          {selectedFile ? (
            <BranchFilePreview
              repoUrl={repoUrl}
              branchName={branch.branch}
              filePath={selectedFile}
            />
          ) : (
            <div className="card-dark p-8 text-center text-slate-600 text-xs font-mono">
              Select a file to preview
            </div>
          )}
        </div>
      </div>

      {registerMut.isError && (
        <p className="text-xs text-red-400 font-mono break-all">
          {registerMut.error instanceof Error ? registerMut.error.message : "Registration failed"}
        </p>
      )}
    </div>
  );
}

function BranchFilePreview({
  repoUrl,
  branchName,
  filePath,
}: {
  repoUrl: string;
  branchName: string;
  filePath: string;
}) {
  const { data, isLoading, isError, error } = usePluginPackBranchFile(
    repoUrl,
    branchName,
    filePath,
  );

  if (isLoading) {
    return <div className="card-dark p-8 text-center text-slate-600 text-xs font-mono">Loading...</div>;
  }

  if (isError) {
    return (
      <div className="card-dark p-8 text-center text-amber-400 text-xs font-mono break-all">
        {error instanceof Error ? error.message : "Could not load file."}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-mono text-slate-500">{filePath}</span>
        <span className="text-xs text-slate-600">read-only</span>
      </div>
      <textarea
        className="w-full h-96 bg-[#0d1117] border border-white/[0.08] rounded p-3 text-xs font-mono text-slate-300 resize-y focus:outline-none"
        value={data?.content ?? ""}
        readOnly
        spellCheck={false}
      />
    </div>
  );
}


// ---------------------------------------------------------------------------
// Pack editor: header + file list + textarea
// ---------------------------------------------------------------------------

function PackEditor({ pack, onDeleted }: { pack: PluginPack; onDeleted: () => void }) {
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [copyStatus, setCopyStatus] = useState<string | null>(null);
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

  async function handleCopyCloneCommand() {
    if (!pack.repo_url) return;
    setCopyStatus(null);
    try {
      await navigator.clipboard.writeText(cloneCommandForPack(pack));
      setCopyStatus("Copied");
    } catch {
      setCopyStatus("Copy failed");
    }
  }

  return (
    <div className="space-y-4">
      {/* Pack header */}
      <div className="card-dark px-4 py-3 flex items-start justify-between gap-4">
        <div>
          <div className="text-sm font-medium text-slate-200">
            {pack.repo_branch ?? pack.manifest_name ?? pack.name}
          </div>
          <div className="text-xs text-slate-500 mt-0.5">v{pack.version}</div>
          {pack.repo_branch && (
            <div className="text-xs text-slate-600 mt-1">{pack.manifest_name ?? pack.name}</div>
          )}
          {pack.repo_url && (
            <div className="text-xs text-slate-600 mt-1 font-mono break-all">{pack.repo_url}</div>
          )}
          {pack.repo_branch && (
            <div className="text-xs text-slate-600 mt-1 font-mono break-all">
              branch {pack.repo_branch}
            </div>
          )}
          {pack.last_commit_sha && (
            <div className="text-xs text-slate-600 mt-1 font-mono">
              commit {pack.last_commit_sha.slice(0, 12)}
            </div>
          )}
          {pack.description && (
            <div className="text-xs text-slate-500 mt-1">{pack.description}</div>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {pack.repo_url && (
            <>
              <button
                className="btn-ghost !text-xs"
                onClick={handleCopyCloneCommand}
              >
                {copyStatus ?? "Copy clone"}
              </button>
              <button
                className="btn-ghost !text-xs"
                onClick={handlePull}
                disabled={pullMut.isPending}
              >
                {pullMut.isPending ? "Pulling..." : "Pull latest"}
              </button>
            </>
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

function cloneCommandForPack(pack: PluginPack) {
  if (!pack.repo_url) return "";
  return pack.repo_branch
    ? `git clone --branch ${pack.repo_branch} ${pack.repo_url}`
    : `git clone ${pack.repo_url}`;
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

function AddPackModal({ starter, onClose }: { starter: StarterPack | null; onClose: () => void }) {
  const [repoUrl, setRepoUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [starterCopyStatus, setStarterCopyStatus] = useState<string | null>(null);

  const discoverMut = useDiscoverPluginPackBranches();
  const registerMut = useRegisterPluginPack();

  async function handleDiscover() {
    if (!repoUrl.trim()) return;
    setError(null);
    try {
      await discoverMut.mutateAsync(repoUrl.trim());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Branch discovery failed");
    }
  }

  async function handleRegisterBranch(branch: PluginPackBranch) {
    const discoveredRepoUrl = discoverMut.data?.repo_url ?? repoUrl.trim();
    if (!discoveredRepoUrl || !branch.valid || !branch.has_pack || branch.registered_pack_id) return;
    setError(null);
    try {
      await registerMut.mutateAsync({ repo_url: discoveredRepoUrl, repo_branch: branch.branch });
      await discoverMut.mutateAsync(discoveredRepoUrl);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registration failed");
    }
  }

  const discoveredBranches = discoverMut.data?.branches ?? [];
  const validBranches = discoveredBranches.filter((branch) => branch.has_pack && branch.valid);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-surface-card border border-white/[0.08] rounded-lg shadow-xl w-full max-w-2xl p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium text-slate-200">Add Swap Packs From GitHub</h3>
          <button className="text-slate-500 hover:text-slate-300 text-xs" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="space-y-3">
          <p className="text-xs text-slate-500">
            Provide a public GitHub repository URL. Branches with a valid root{" "}
            <code className="font-mono">plugin.yaml</code> can be registered as separate Swap Packs.
          </p>
          {starter?.repo_url && (
            <div className="grid grid-cols-2 gap-2">
              <button
                className="btn-ghost !text-xs"
                onClick={() => setRepoUrl(starter.repo_url ?? "")}
              >
                Use starter repo
              </button>
              <button className="btn-ghost !text-xs" onClick={() => openStarterRepo(starter)}>
                Open public repo
              </button>
            </div>
          )}
          {starter?.clone_command && (
            <button
              className="btn-ghost w-full !text-xs"
              onClick={() => copyStarterClone(starter, setStarterCopyStatus)}
            >
              {starterCopyStatus ?? "Copy starter clone command"}
            </button>
          )}
          <PackStructureHint />
          <div className="flex gap-2">
            <input
              type="text"
              className="input-dark w-full text-xs font-mono"
              placeholder="https://github.com/org/my-rulebook"
              value={repoUrl}
              onChange={(e) => setRepoUrl(e.target.value)}
            />
            <button
              className="btn-primary !text-xs shrink-0"
              onClick={handleDiscover}
              disabled={discoverMut.isPending || !repoUrl.trim()}
            >
              {discoverMut.isPending ? "Loading..." : "Show branches"}
            </button>
          </div>
        </div>

        {discoverMut.data && (
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs">
              <span className="font-mono text-slate-500">
                {validBranches.length} pack branch{validBranches.length === 1 ? "" : "es"} found
              </span>
              <span className="font-mono text-slate-600">
                {discoveredBranches.length} branch{discoveredBranches.length === 1 ? "" : "es"} scanned
              </span>
            </div>
            <div className="max-h-80 overflow-auto rounded border border-white/[0.08] divide-y divide-white/[0.06]">
              {discoveredBranches.map((branch) => (
                <BranchDiscoveryRow
                  key={branch.branch}
                  branch={branch}
                  isRegistering={registerMut.isPending}
                  onRegister={() => handleRegisterBranch(branch)}
                />
              ))}
              {discoveredBranches.length === 0 && (
                <div className="px-3 py-6 text-center text-xs text-slate-600">
                  No branches returned by GitHub.
                </div>
              )}
            </div>
          </div>
        )}

        {error && (
          <p className="text-xs text-red-400 font-mono break-all">{error}</p>
        )}
      </div>
    </div>
  );
}

function BranchDiscoveryRow({
  branch,
  isRegistering,
  onRegister,
}: {
  branch: PluginPackBranch;
  isRegistering: boolean;
  onRegister: () => void;
}) {
  const canRegister = branch.has_pack && branch.valid && !branch.registered_pack_id;
  const buttonLabel = branch.registered_pack_id
    ? "Registered"
    : branch.has_pack && branch.valid
      ? "Register"
      : branch.has_pack
        ? "Invalid"
        : "No pack";

  return (
    <div className="px-3 py-2.5 flex items-start justify-between gap-3 bg-black/10">
      <div className="min-w-0">
        <div className="text-xs font-mono text-slate-300 break-all">{branch.branch}</div>
        {branch.has_pack && branch.valid ? (
          <div className="text-xs text-slate-500 mt-1">
            {branch.name} v{branch.version}
            {branch.description ? <span className="ml-2">{branch.description}</span> : null}
          </div>
        ) : (
          <div className="text-xs text-slate-600 mt-1">{branch.error ?? "No Swap Pack found."}</div>
        )}
        {branch.commit_sha && (
          <div className="text-[11px] text-slate-700 font-mono mt-1">
            {branch.commit_sha.slice(0, 12)}
          </div>
        )}
      </div>
      <button
        className="btn-ghost !text-xs shrink-0"
        onClick={onRegister}
        disabled={!canRegister || isRegistering}
      >
        {isRegistering && canRegister ? "Cloning..." : buttonLabel}
      </button>
    </div>
  );
}

function PackStructureHint() {
  return (
    <div className="rounded border border-white/[0.08] bg-black/20 p-3">
      <div className="text-[11px] font-mono text-slate-500 mb-2">Expected pack root</div>
      <pre className="text-[11px] leading-5 font-mono text-slate-400 whitespace-pre-wrap">{`plugin.yaml
AGENTS.md
README.md
prompts/
  investigation.md
  phase2.md
  phase3.md
  scorecard.md
  docs/
    YOUR_DOC_NAME.md
examples/
  sample.md
.codex/
  skills/
    tune-swap-pack/
      SKILL.md`}</pre>
      <p className="text-[11px] text-slate-500 mt-2">
        Document prompt names should match the <code className="font-mono">docs.name</code> entries in{" "}
        <code className="font-mono">plugin.yaml</code>.
      </p>
    </div>
  );
}
