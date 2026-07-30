import { useEffect, useRef, useState } from "react";
import {
  useDeletePluginPack,
  usePluginPackBranchFile,
  usePluginPackBranchFiles,
  usePluginPackBranches,
  usePluginPackFile,
  usePluginPackFiles,
  usePluginPacks,
  usePullPluginPack,
  useRegisterPluginPack,
  useSavePluginPackFile,
  usePackWorkspace,
  useProjects,
  useStarterPack,
  useUpdatePackWorkspace,
} from "../hooks/useProjects";
import type {
  NexusPackCatalogItem,
  NexusPackWorkspace,
  PluginPack,
  PluginPackBranch,
  Project,
  StarterPack,
} from "../types";

type Selection =
  | { type: "registered"; id: number }
  | { type: "branch"; branch: string };

function errorText(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function packTitle(pack: PluginPack): string {
  return pack.repo_branch ?? pack.name;
}

function packSubtitle(pack: PluginPack): string {
  const parts = [`v${pack.version}`];
  if (pack.manifest_name && pack.manifest_name !== pack.name) {
    parts.push(pack.manifest_name);
  }
  if (pack.repo_branch) {
    parts.push(pack.repo_branch);
  }
  parts.push(
    pack.certification_status === "certified"
      ? "certified"
      : `not certified${pack.blocked_reason ? `: ${pack.blocked_reason}` : ""}`,
  );
  return parts.join("  ·  ");
}

function branchCloneCommand(branch: PluginPackBranch): string {
  return `git clone --branch ${branch.branch} --single-branch ${branch.repo_url}`;
}

async function copyText(text: string): Promise<void> {
  await navigator.clipboard.writeText(text);
}

interface Props {
  isSuperadmin?: boolean;
}

export default function PluginPacksPage({ isSuperadmin = false }: Props) {
  const { data: projectsData, isLoading: projectsLoading } = useProjects();
  const projects = projectsData?.projects ?? [];
  const [projectId, setProjectId] = useState("");
  const [organizationPackId, setOrganizationPackId] = useState<number | null>(null);
  const [privateVersionIds, setPrivateVersionIds] = useState<number[]>([]);
  const workspaceQuery = usePackWorkspace(projectId || null);
  const updateWorkspace = useUpdatePackWorkspace();

  useEffect(() => {
    if (!projectId && projects.length > 0) {
      setProjectId(projects[0].id);
    }
  }, [projectId, projects]);

  useEffect(() => {
    const workspace = workspaceQuery.data;
    if (workspace) {
      setOrganizationPackId(workspace.selected_organization_pack_id);
      setPrivateVersionIds(workspace.selected_private_version_ids);
    }
  }, [workspaceQuery.data]);

  const selectedProject = projects.find((project) => project.id === projectId) ?? null;
  const workspace = workspaceQuery.data;

  function saveWorkspace() {
    if (!projectId) return;
    updateWorkspace.mutate({
      projectId,
      organizationPackId,
      privateVersionIds,
    });
  }

  return (
    <div className="space-y-6">
      <section className="border-b border-white/[0.08] pb-5">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold text-slate-100">Plugin Packs</h1>
            <p className="mt-1 text-sm text-slate-500">
              Choose the plugin pack that defines this project's documentation behavior.
            </p>
          </div>
          <div className="w-full sm:w-80">
            <label className="mb-1 block text-xs font-mono uppercase tracking-wider text-slate-600">
              Project
            </label>
            <select
              aria-label="Project"
              className="input-dark"
              value={projectId}
              onChange={(event) => setProjectId(event.target.value)}
              disabled={projectsLoading || projects.length === 0}
            >
              <option value="">Select a project</option>
              {projects.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.display_name} ({project.id})
                </option>
              ))}
            </select>
          </div>
        </div>
      </section>

      {!projectId && !projectsLoading && (
        <div className="card-dark p-8 text-center text-sm text-slate-500">
          Add or select a project to configure its documentation pack.
        </div>
      )}

      {projectId && (
        <PackWorkspacePanel
          project={selectedProject}
          workspace={workspace}
          isLoading={workspaceQuery.isLoading}
          error={workspaceQuery.error}
          organizationPackId={organizationPackId}
          onOrganizationPackChange={setOrganizationPackId}
          onSave={saveWorkspace}
          isSaving={updateWorkspace.isPending}
          saveError={updateWorkspace.error}
        />
      )}

      {isSuperadmin && (
        <details className="border-t border-white/[0.08] pt-5">
          <summary className="cursor-pointer text-sm font-mono text-slate-500 hover:text-slate-300">
            Advanced/admin: legacy registry and file/Git administration
          </summary>
          <div className="mt-5">
            <LegacyPluginPacksAdmin />
          </div>
        </details>
      )}
    </div>
  );
}

function PackWorkspacePanel({
  project,
  workspace,
  isLoading,
  error,
  organizationPackId,
  onOrganizationPackChange,
  onSave,
  isSaving,
  saveError,
}: {
  project: Project | null;
  workspace: NexusPackWorkspace | undefined;
  isLoading: boolean;
  error: unknown;
  organizationPackId: number | null;
  onOrganizationPackChange: (id: number | null) => void;
  onSave: () => void;
  isSaving: boolean;
  saveError: unknown;
}) {
  if (isLoading) {
    return <p className="text-sm font-mono text-slate-500">Loading pack workspace...</p>;
  }
  if (error || !workspace) {
    return (
      <div className="card-dark p-5 text-sm text-amber-400">
        Could not load the documentation pack workspace.
      </div>
    );
  }

  return (
    <section className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-200">
            {project?.display_name ?? workspace.project_id}
          </h2>
          <p className="mt-1 text-xs text-slate-600 font-mono">{workspace.project_id}</p>
        </div>
        <button
          type="button"
          className="btn-primary"
          onClick={onSave}
          disabled={isSaving}
        >
          {isSaving ? "Saving..." : "Save pack selection"}
        </button>
      </div>

      <div className="max-w-2xl">
        <PackSelectionList
          title="Plugin Pack"
          description="Select the certified plugin pack that shapes this project's documentation, or use the organization default."
          packs={workspace.organization_packs}
          selectedId={organizationPackId}
          onSelect={onOrganizationPackChange}
        />
      </div>

      {saveError !== null && saveError !== undefined && (
        <p className="text-sm text-red-400 font-mono break-all">
          {errorText(saveError, "Could not save pack selection.")}
        </p>
      )}
    </section>
  );
}

function PackSelectionList({
  title,
  description,
  packs,
  selectedId,
  onSelect,
}: {
  title: string;
  description: string;
  packs: NexusPackCatalogItem[];
  selectedId: number | null;
  onSelect: (id: number | null) => void;
}) {
  return (
    <div>
      <h3 className="text-sm font-mono text-slate-400">{title}</h3>
      <p className="mt-1 text-xs text-slate-600">{description}</p>
      <div className="mt-3 space-y-2">
        <label className="flex cursor-pointer items-start gap-3 rounded border border-white/[0.08] px-3 py-2.5 hover:bg-white/[0.04]">
          <input
            type="radio"
            name="organization-pack"
            checked={selectedId === null}
            onChange={() => onSelect(null)}
            className="mt-1 h-4 w-4 accent-accent-primary"
          />
          <span>
            <span className="block text-sm text-slate-200">Organization default</span>
            <span className="mt-1 block text-xs text-slate-500">Use the approved Nexus documentation baseline.</span>
          </span>
        </label>
        {packs.map((pack) => (
          <label
            key={pack.id}
            className="flex cursor-pointer items-start gap-3 rounded border border-white/[0.08] px-3 py-2.5 hover:bg-white/[0.04]"
          >
            <input
              type="radio"
              name="organization-pack"
              checked={selectedId === pack.id}
              onChange={() => onSelect(pack.id)}
              className="mt-1 h-4 w-4 accent-accent-primary"
            />
            <span className="min-w-0">
              <span className="block text-sm text-slate-200">{pack.name} v{pack.version}</span>
              {pack.description && <span className="mt-1 block text-xs text-slate-500">{pack.description}</span>}
            </span>
          </label>
        ))}
        {packs.length === 0 && <p className="text-xs text-slate-600">No organization packs available.</p>}
      </div>
    </div>
  );
}

function LegacyPluginPacksAdmin() {
  const { data: packs = [], isLoading } = usePluginPacks();
  const { data: starter } = useStarterPack();
  const {
    data: discovery,
    isFetching: isFetchingBranches,
    error: branchError,
  } = usePluginPackBranches(starter?.repo_url ?? null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [showAdd, setShowAdd] = useState(false);

  const availableBranches =
    discovery?.branches.filter((branch) => !branch.registered) ?? [];
  const selectedPack =
    selection?.type === "registered"
      ? (packs as PluginPack[]).find((pack) => pack.id === selection.id) ?? null
      : null;
  const selectedBranch =
    selection?.type === "branch"
      ? availableBranches.find((branch) => branch.branch === selection.branch) ?? null
      : null;
  const totalCount = (packs as PluginPack[]).length + availableBranches.length;

  function openPublicRepo() {
    if (starter?.repo_url) {
      window.open(starter.repo_url, "_blank", "noopener,noreferrer");
    }
  }

  async function copyStarterClone() {
    if (starter?.clone_url) {
      await copyText(starter.clone_url);
    }
  }

  return (
    <div className="grid grid-cols-12 gap-6">
      <div className="col-span-4 space-y-4">
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-sm font-mono text-slate-400">
            Swap Packs
            {totalCount > 0 && (
              <span className="ml-2 text-xs text-slate-600">({totalCount})</span>
            )}
          </h3>
          <div className="flex items-center gap-2">
            <button
              className="btn-ghost !text-xs"
              onClick={openPublicRepo}
              disabled={!starter?.repo_url}
            >
              Public repo
            </button>
            <button
              className="btn-ghost !text-xs"
              onClick={copyStarterClone}
              disabled={!starter?.clone_url}
            >
              Copy clone
            </button>
            <button className="btn-ghost !text-xs" onClick={() => setShowAdd(true)}>
              + Add Swap
            </button>
          </div>
        </div>

        {isFetchingBranches && (
          <p className="text-xs text-slate-600 font-mono">Loading GitHub branches...</p>
        )}
        {branchError && (
          <p className="text-xs text-amber-400 font-mono break-all">
            Could not load GitHub branches.
          </p>
        )}
        {isLoading && (
          <p className="text-xs text-slate-500 font-mono">Loading...</p>
        )}

        <div className="space-y-1">
          {(packs as PluginPack[]).map((pack) => (
            <button
              key={`registered-${pack.id}`}
              className={`w-full text-left px-3 py-2.5 rounded text-sm transition-all ${
                selection?.type === "registered" && selection.id === pack.id
                  ? "bg-white/[0.08] text-slate-200"
                  : "text-slate-400 hover:bg-white/[0.04] hover:text-slate-300"
              }`}
              onClick={() => setSelection({ type: "registered", id: pack.id })}
            >
              <div className="font-medium">{packTitle(pack)}</div>
              <div className="text-xs text-slate-600 mt-0.5">{packSubtitle(pack)}</div>
            </button>
          ))}

          {availableBranches.map((branch) => (
            <button
              key={`branch-${branch.branch}`}
              className={`w-full text-left px-3 py-2.5 rounded text-sm transition-all ${
                selection?.type === "branch" && selection.branch === branch.branch
                  ? "bg-white/[0.08] text-slate-200"
                  : "text-slate-400 hover:bg-white/[0.04] hover:text-slate-300"
              }`}
              onClick={() => setSelection({ type: "branch", branch: branch.branch })}
            >
              <div className="font-medium">{branch.branch}</div>
              <div className="text-xs text-slate-600 mt-0.5">
                v{branch.version}  {branch.manifest_name}{" "}
                <span className="text-emerald-500">available</span>
              </div>
            </button>
          ))}

          {!isLoading && totalCount === 0 && (
            <p className="text-xs text-slate-600 font-mono px-1">No packs found.</p>
          )}
        </div>
      </div>

      <div className="col-span-8">
        {selectedPack ? (
          <PackEditor
            pack={selectedPack}
            onDeleted={() => setSelection(null)}
          />
        ) : selectedBranch ? (
          <AvailableBranchPanel
            branch={selectedBranch}
            onRegistered={(pack) => setSelection({ type: "registered", id: pack.id })}
          />
        ) : (
          <div className="card-dark p-12 text-center text-slate-500 text-sm">
            Select a swap pack.
          </div>
        )}
      </div>

      {showAdd && (
        <AddPackModal
          starter={starter ?? null}
          onClose={() => setShowAdd(false)}
          onRegistered={(pack) => {
            setSelection({ type: "registered", id: pack.id });
            setShowAdd(false);
          }}
        />
      )}
    </div>
  );
}

function PackEditor({
  pack,
  onDeleted,
}: {
  pack: PluginPack;
  onDeleted: () => void;
}) {
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const pullMut = usePullPluginPack();
  const deleteMut = useDeletePluginPack();
  const { data: files = [] } = usePluginPackFiles(pack.id);

  useEffect(() => {
    setSelectedFile(null);
  }, [pack.id]);

  async function handleDelete() {
    if (!confirm(`Delete pack "${packTitle(pack)}"? Projects using it will be unassigned.`)) {
      return;
    }
    await deleteMut.mutateAsync(pack.id);
    onDeleted();
  }

  async function handlePull() {
    await pullMut.mutateAsync(pack.id);
  }

  async function copyClone() {
    if (pack.repo_url) {
      await copyText(branchCloneCommand({
        branch: pack.repo_branch ?? "main",
        name: pack.name,
        manifest_name: pack.manifest_name ?? pack.name,
        version: pack.version,
        description: pack.description,
        repo_url: pack.repo_url,
        commit_sha: pack.last_commit_sha ?? "",
        registered: true,
        registered_pack_id: pack.id,
      }));
    }
  }

  return (
    <div className="space-y-4">
      <div className="card-dark px-4 py-3 flex items-start justify-between gap-4">
        <div>
          <div className="text-sm font-medium text-slate-200">{packTitle(pack)}</div>
          <div className="text-xs text-slate-500 mt-0.5">{packSubtitle(pack)}</div>
          {pack.repo_url && (
            <div className="text-xs text-slate-600 mt-1 font-mono break-all">
              {pack.repo_url}
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
              <button className="btn-ghost !text-xs" onClick={copyClone}>
                Copy clone
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

      <div className="grid grid-cols-12 gap-4">
        <div className="col-span-4">
          <FileList files={files} selectedFile={selectedFile} onSelect={setSelectedFile} />
        </div>
        <div className="col-span-8">
          {selectedFile ? (
            <FileEditor packId={pack.id} filePath={selectedFile} />
          ) : (
            <div className="card-dark p-8 text-center text-slate-600 text-xs font-mono">
              Select a file
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function AvailableBranchPanel({
  branch,
  onRegistered,
}: {
  branch: PluginPackBranch;
  onRegistered: (pack: PluginPack) => void;
}) {
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const registerMut = useRegisterPluginPack();
  const { data: files = [], isLoading, error } = usePluginPackBranchFiles(
    branch.repo_url,
    branch.branch,
  );

  useEffect(() => {
    setSelectedFile(null);
  }, [branch.branch, branch.repo_url]);

  useEffect(() => {
    if (!selectedFile && files.length > 0) {
      setSelectedFile(files[0]);
    }
  }, [files, selectedFile]);

  async function registerBranch() {
    const pack = await registerMut.mutateAsync({
      repo_url: branch.repo_url,
      repo_branch: branch.branch,
    });
    onRegistered(pack);
  }

  async function copyClone() {
    await copyText(branchCloneCommand(branch));
  }

  return (
    <div className="space-y-4">
      <div className="card-dark px-4 py-3 flex items-start justify-between gap-4">
        <div>
          <div className="text-sm font-medium text-slate-200">{branch.branch}</div>
          <div className="text-xs text-slate-500 mt-0.5">
            v{branch.version}  manifest {branch.manifest_name}
          </div>
          <div className="text-xs text-slate-600 mt-1 font-mono break-all">
            {branch.repo_url}
          </div>
          <div className="text-xs text-slate-600 mt-1 font-mono">
            commit {branch.commit_sha.slice(0, 12)}
          </div>
          {branch.description && (
            <div className="text-xs text-slate-500 mt-1">{branch.description}</div>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button className="btn-ghost !text-xs" onClick={copyClone}>
            Copy clone
          </button>
          <button
            className="btn-primary !text-xs !py-1.5 !px-3"
            onClick={registerBranch}
            disabled={registerMut.isPending}
          >
            {registerMut.isPending ? "Registering..." : "Register branch"}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-12 gap-4">
        <div className="col-span-4">
          {isLoading ? (
            <div className="card-dark p-8 text-center text-slate-600 text-xs font-mono">
              Loading files...
            </div>
          ) : error ? (
            <div className="card-dark p-4 text-xs text-amber-400 font-mono break-all">
              {errorText(error, "Could not load files.")}
            </div>
          ) : (
            <FileList files={files} selectedFile={selectedFile} onSelect={setSelectedFile} />
          )}
        </div>
        <div className="col-span-8">
          {selectedFile ? (
            <BranchFilePreview
              repoUrl={branch.repo_url}
              repoBranch={branch.branch}
              filePath={selectedFile}
            />
          ) : (
            <div className="card-dark p-8 text-center text-slate-600 text-xs font-mono">
              Select a file
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function FileList({
  files,
  selectedFile,
  onSelect,
}: {
  files: string[];
  selectedFile: string | null;
  onSelect: (file: string) => void;
}) {
  return (
    <div className="card-dark p-3 space-y-0.5">
      <div className="text-xs font-mono text-slate-600 mb-2 uppercase tracking-wider">
        Files
      </div>
      {files.map((file) => (
        <button
          key={file}
          className={`w-full text-left px-2 py-1.5 rounded text-xs font-mono transition-all break-all ${
            selectedFile === file
              ? "bg-white/[0.08] text-slate-200"
              : "text-slate-500 hover:bg-white/[0.04] hover:text-slate-300"
          }`}
          onClick={() => onSelect(file)}
        >
          {file}
        </button>
      ))}
      {files.length === 0 && (
        <p className="text-xs text-slate-600 px-2">No editable files found.</p>
      )}
    </div>
  );
}

function FileEditor({ packId, filePath }: { packId: number; filePath: string }) {
  const { data, isLoading } = usePluginPackFile(packId, filePath);
  const saveMut = useSavePluginPackFile();
  const [draft, setDraft] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const lastFilePath = useRef(filePath);

  useEffect(() => {
    if (lastFilePath.current !== filePath) {
      lastFilePath.current = filePath;
      setDraft(null);
      setSaveError(null);
    }
  }, [filePath]);

  const content = draft ?? data?.content ?? "";
  const isDirty = draft !== null && draft !== data?.content;

  async function handleSave() {
    setSaveError(null);
    try {
      await saveMut.mutateAsync({ packId, filePath, content });
      setDraft(null);
    } catch (err) {
      setSaveError(errorText(err, "Save failed"));
    }
  }

  if (isLoading) {
    return (
      <div className="card-dark p-8 text-center text-slate-600 text-xs font-mono">
        Loading...
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs font-mono text-slate-500 break-all">{filePath}</span>
        <button
          className="btn-primary !text-xs !py-1 !px-3 shrink-0"
          onClick={handleSave}
          disabled={!isDirty || saveMut.isPending}
        >
          {saveMut.isPending ? "Saving..." : "Save"}
        </button>
      </div>
      <textarea
        className="w-full h-96 bg-[#0d1117] border border-white/[0.08] rounded p-3 text-xs font-mono text-slate-300 resize-y focus:outline-none focus:border-indigo-500/50 transition-colors"
        value={content}
        onChange={(event) => setDraft(event.target.value)}
        spellCheck={false}
      />
      {saveError && (
        <p className="text-xs text-red-400 font-mono break-all">{saveError}</p>
      )}
    </div>
  );
}

function BranchFilePreview({
  repoUrl,
  repoBranch,
  filePath,
}: {
  repoUrl: string;
  repoBranch: string;
  filePath: string;
}) {
  const { data, isLoading, error } = usePluginPackBranchFile(repoUrl, repoBranch, filePath);

  if (isLoading) {
    return (
      <div className="card-dark p-8 text-center text-slate-600 text-xs font-mono">
        Loading...
      </div>
    );
  }

  if (error) {
    return (
      <div className="card-dark p-4 text-xs text-amber-400 font-mono break-all">
        {errorText(error, "Could not load file.")}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="text-xs font-mono text-slate-500 break-all">{filePath}</div>
      <textarea
        className="w-full h-96 bg-[#0d1117] border border-white/[0.08] rounded p-3 text-xs font-mono text-slate-300 resize-y focus:outline-none"
        value={data?.content ?? ""}
        readOnly
        spellCheck={false}
      />
    </div>
  );
}

function AddPackModal({
  starter,
  onClose,
  onRegistered,
}: {
  starter: StarterPack | null;
  onClose: () => void;
  onRegistered: (pack: PluginPack) => void;
}) {
  const [repoInput, setRepoInput] = useState(starter?.repo_url ?? "");
  const [discoverUrl, setDiscoverUrl] = useState<string | null>(starter?.repo_url ?? null);
  const [error, setError] = useState<string | null>(null);
  const registerMut = useRegisterPluginPack();
  const { data, isFetching, error: discoverError, refetch } = usePluginPackBranches(discoverUrl);

  useEffect(() => {
    if (starter?.repo_url && !repoInput && !discoverUrl) {
      setRepoInput(starter.repo_url);
      setDiscoverUrl(starter.repo_url);
    }
  }, [discoverUrl, repoInput, starter]);

  async function discover() {
    const nextUrl = repoInput.trim();
    if (!nextUrl) return;
    setError(null);
    if (discoverUrl === nextUrl) {
      await refetch();
      return;
    }
    setDiscoverUrl(nextUrl);
  }

  async function registerBranch(branch: PluginPackBranch) {
    setError(null);
    try {
      const pack = await registerMut.mutateAsync({
        repo_url: branch.repo_url,
        repo_branch: branch.branch,
      });
      onRegistered(pack);
    } catch (err) {
      setError(errorText(err, "Registration failed"));
    }
  }

  const branches = data?.branches ?? [];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-surface-card border border-white/[0.08] rounded-lg shadow-xl w-full max-w-2xl p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium text-slate-200">Add Swap Pack</h3>
          <button className="text-slate-500 hover:text-slate-300 text-xs" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="flex items-center gap-2">
          <input
            type="text"
            className="input-dark w-full text-xs font-mono"
            placeholder="https://github.com/org/swap-pack"
            value={repoInput}
            onChange={(event) => setRepoInput(event.target.value)}
          />
          <button
            className="btn-ghost !text-xs shrink-0"
            onClick={discover}
            disabled={isFetching || !repoInput.trim()}
          >
            {isFetching ? "Loading..." : "Load branches"}
          </button>
        </div>

        <div className="space-y-2 max-h-96 overflow-y-auto pr-1">
          {branches.map((branch) => (
            <div
              key={branch.branch}
              className="border border-white/[0.08] rounded px-3 py-2 flex items-start justify-between gap-3"
            >
              <div>
                <div className="text-sm text-slate-200 font-medium">{branch.branch}</div>
                <div className="text-xs text-slate-600 mt-0.5">
                  v{branch.version}  {branch.manifest_name}
                </div>
                {branch.description && (
                  <div className="text-xs text-slate-500 mt-1">{branch.description}</div>
                )}
              </div>
              <button
                className="btn-primary !text-xs !py-1 !px-3 shrink-0"
                onClick={() => registerBranch(branch)}
                disabled={branch.registered || registerMut.isPending}
              >
                {branch.registered ? "Registered" : "Register"}
              </button>
            </div>
          ))}
          {!isFetching && branches.length === 0 && (
            <p className="text-xs text-slate-600 font-mono px-1">No valid branches found.</p>
          )}
        </div>

        {(error || discoverError) && (
          <p className="text-xs text-red-400 font-mono break-all">
            {error ?? errorText(discoverError, "Branch discovery failed")}
          </p>
        )}
      </div>
    </div>
  );
}
