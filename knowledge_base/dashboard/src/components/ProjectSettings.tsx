import { useState, useEffect } from "react";
import { parseApiError } from "../lib/errors";
import { usePlatforms, useModels } from "../hooks/useDiscovery";
import {
  useUpdateProject,
  useProjectUsage,
  useDeleteProject,
  useInstructionPack,
  useRepairLatestGitUpdate,
  useRegisterWebhook,
  useUpdateGithubToken,
  usePluginPacks,
  useAssignPluginPack,
} from "../hooks/useProjects";
import type { Project } from "../types";
import { deriveGitSummary } from "../lib/gitSummary";
import ProjectMetaGrid from "./ProjectMetaGrid";
import ModelSelectionPanel from "./ModelSelectionPanel";
import InstructionPackPanel from "./InstructionPackPanel";
import ProjectUsagePanel from "./ProjectUsagePanel";
import DangerZone from "./DangerZone";
import ProjectConnectorBindings from "./connectors/ProjectConnectorBindings";

interface Props {
  project: Project | null;
  onDeleted?: () => void;
}

export default function ProjectSettings({ project, onDeleted }: Props) {
  const updateProject = useUpdateProject();
  const deleteProject = useDeleteProject();
  const repairLatestGitUpdate = useRepairLatestGitUpdate();
  const registerWebhook = useRegisterWebhook();
  const updateGithubToken = useUpdateGithubToken();
  const usage = useProjectUsage(project?.id ?? null);
  const instructionPack = useInstructionPack();
  const { data: pluginPacks } = usePluginPacks();
  const assignPluginPack = useAssignPluginPack();
  const { data: platformsData } = usePlatforms();
  const { data: modelsData } = useModels();
  const [displayName, setDisplayName] = useState("");
  const [platform, setPlatform] = useState("");
  const [docProvider, setDocProvider] = useState("");
  const [investigationModel, setInvestigationModel] = useState("");
  const [writeModel, setWriteModel] = useState("");
  const [packDir, setPackDir] = useState("");
  const [saved, setSaved] = useState(false);
  const [confirmText, setConfirmText] = useState("");
  const [newToken, setNewToken] = useState("");

  useEffect(() => {
    if (project) {
      setDisplayName(project.display_name);
      setPlatform(project.platform);
      setDocProvider(project.doc_generation_provider ?? "");
      setInvestigationModel(localStorage.getItem(`project_investigation_model_${project.id}`) ?? "");
      setWriteModel(localStorage.getItem(`project_write_model_${project.id}`) ?? "");
      setPackDir(project.instruction_pack_dir ?? "");
      setSaved(false);
      setConfirmText("");
    }
  }, [project?.id]);

  function handleWriteModelChange(value: string) {
    setWriteModel(value);
    if (project) {
      if (value) {
        localStorage.setItem(`project_write_model_${project.id}`, value);
      } else {
        localStorage.removeItem(`project_write_model_${project.id}`);
      }
    }
  }

  function handleInvestigationModelChange(value: string) {
    setInvestigationModel(value);
    if (project) {
      if (value) {
        localStorage.setItem(`project_investigation_model_${project.id}`, value);
      } else {
        localStorage.removeItem(`project_investigation_model_${project.id}`);
      }
    }
  }

  if (!project) return null;

  const hasChanges =
    displayName !== project.display_name ||
    platform !== project.platform ||
    docProvider !== (project.doc_generation_provider ?? "") ||
    packDir !== (project.instruction_pack_dir ?? "");
  const platforms = platformsData?.platforms ?? [];
  const analysisModels = modelsData?.models.filter((m) => m.supports_analysis) ?? [];
  const writingModels = modelsData?.models.filter((m) => m.supports_writing) ?? [];
  const defaultAnalysisModel = analysisModels.find((m) => m.is_default_analysis);
  const defaultInvestigationModel = analysisModels.find((m) => m.is_default_investigation);
  const defaultWritingModel = writingModels.find((m) => m.is_default_writing);
  const defaultAnalysisLabel = defaultAnalysisModel
    ? `${defaultAnalysisModel.label} (${defaultAnalysisModel.id})`
    : "server config";
  const defaultWritingLabel = defaultWritingModel
    ? `${defaultWritingModel.label} (${defaultWritingModel.id})`
    : "server config";
  const defaultInvestigationLabel = defaultInvestigationModel
    ? `${defaultInvestigationModel.label} (${defaultInvestigationModel.id})`
    : defaultAnalysisLabel;
  const hasKnownDocProvider = docProvider === "" || analysisModels.some(({ id }) => id === docProvider);
  const hasKnownInvestigationModel = investigationModel === "" || analysisModels.some(({ id }) => id === investigationModel);
  const hasKnownWriteModel = writeModel === "" || writingModels.some(({ id }) => id === writeModel);
  const gitSummary = deriveGitSummary(project);

  function handleSave() {
    if (!project || !hasChanges) return;
    updateProject.mutate(
      {
        projectId: project.id,
        data: {
          display_name: displayName !== project.display_name ? displayName : undefined,
          platform: platform !== project.platform ? platform : undefined,
          doc_generation_provider:
            docProvider !== (project.doc_generation_provider ?? "")
              ? docProvider || null
              : undefined,
          instruction_pack_dir:
            packDir !== (project.instruction_pack_dir ?? "")
              ? packDir || null
              : undefined,
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
    <div className="card-dark">
      <div className="px-4 py-3 border-b border-white/[0.05] bg-surface-raised rounded-t">
        <h3 className="text-sm font-mono text-slate-400">Project Settings</h3>
      </div>

      <div className="p-4 space-y-4">
        <ProjectMetaGrid
          project={project}
          gitSummary={gitSummary}
          repairLatestGitUpdate={repairLatestGitUpdate}
          newToken={newToken}
          onNewTokenChange={setNewToken}
          registerWebhook={registerWebhook}
          updateGithubToken={updateGithubToken}
        />

        <hr className="border-white/[0.05]" />

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="block font-mono uppercase tracking-wider text-xs text-slate-600 mb-1">Display Name</label>
            <input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className="input-dark"
            />
          </div>
          <div>
            <label className="block font-mono uppercase tracking-wider text-xs text-slate-600 mb-1">Platform</label>
            <select
              value={platform}
              onChange={(e) => setPlatform(e.target.value)}
              className="input-dark"
            >
              {platforms.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
              {platform && !platforms.some((p) => p.id === platform) && (
                <option value={platform}>{platform}</option>
              )}
            </select>
          </div>
          <ModelSelectionPanel
            project={project}
            docProvider={docProvider}
            onDocProviderChange={setDocProvider}
            investigationModel={investigationModel}
            onInvestigationModelChange={handleInvestigationModelChange}
            writeModel={writeModel}
            onWriteModelChange={handleWriteModelChange}
            packDir={packDir}
            onPackDirChange={setPackDir}
            analysisModels={analysisModels}
            writingModels={writingModels}
            defaultAnalysisLabel={defaultAnalysisLabel}
            defaultInvestigationLabel={defaultInvestigationLabel}
            defaultWritingLabel={defaultWritingLabel}
            defaultAnalysisModelId={defaultAnalysisModel?.id}
            defaultWritingModelId={defaultWritingModel?.id}
            hasKnownDocProvider={hasKnownDocProvider}
            hasKnownInvestigationModel={hasKnownInvestigationModel}
            hasKnownWriteModel={hasKnownWriteModel}
            instructionPackVersion={instructionPack.data?.version}
            pluginPacks={pluginPacks}
            assignPluginPack={assignPluginPack}
          />
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={handleSave}
            disabled={!hasChanges || updateProject.isPending}
            className={`px-4 py-2 text-sm rounded font-medium transition-all ${
              hasChanges
                ? "bg-accent-primary text-surface-bg hover:bg-accent-primary/90"
                : "bg-white/[0.05] text-slate-600 cursor-not-allowed"
            }`}
          >
            {updateProject.isPending ? "Saving..." : "Save Changes"}
          </button>
          {saved && (
            <span className="text-sm text-[#a0e06f] font-medium">Saved!</span>
          )}
          {updateProject.isError && (
            <span className="text-sm text-red-400">
              Failed: {parseApiError(updateProject.error)}
            </span>
          )}
        </div>

        <hr className="border-white/[0.05]" />
        <InstructionPackPanel
          isLoading={instructionPack.isLoading}
          isError={instructionPack.isError}
          data={instructionPack.data}
        />

        <hr className="border-white/[0.05]" />
        <div>
          <h4 className="text-sm font-medium text-slate-200">Runtime evidence</h4>
          <p className="mt-1 text-xs text-slate-500">Connected sources are enabled automatically for this project. Open advanced settings only if you need to limit what documentation runs can read.</p>
        </div>
        <ProjectConnectorBindings projectId={project.id} />

        <hr className="border-white/[0.05]" />
        <ProjectUsagePanel
          isLoading={usage.isLoading}
          isError={usage.isError}
          data={usage.data}
        />

        <hr className="border-white/[0.05]" />
        <DangerZone
          projectId={project.id}
          confirmText={confirmText}
          onConfirmTextChange={setConfirmText}
          deleteProject={deleteProject}
          onDeleted={onDeleted}
        />
      </div>
    </div>
  );
}
