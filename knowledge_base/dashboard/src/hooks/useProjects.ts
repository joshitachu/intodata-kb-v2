import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { DocSource } from "../types";

/**
 * Polling rates in this file are per-query and deliberate. There is no global
 * `refetchInterval` (see `main.tsx`): a query polls only while something is
 * actually in flight server-side, and anything that changes at a known event is
 * invalidated at that event instead.
 *
 * `CATALOGUE_STALE_MS` is for data an operator changes by doing something --
 * installing a pack, editing a prompt. It is never polled; the mutation that
 * changes it invalidates it.
 */
const CATALOGUE_STALE_MS = 5 * 60_000;
/** Data derived from a completed run: invalidated by the run-completion effect in `App.tsx`. */
const RUN_ARTIFACT_STALE_MS = 60_000;

export function useProjects() {
  return useQuery({
    queryKey: ["projects"],
    queryFn: api.listProjects,
    // The list is the progress indicator for a scan or generation, so it polls
    // only while one of those is running -- and stops the moment none is.
    refetchInterval: (query) => {
      const projects = query.state.data?.projects ?? [];
      return projects.some((p) => p.status === "scanning" || p.status === "generating") ? 10_000 : false;
    },
    staleTime: 10_000,
  });
}

export function useAddRepo() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.addRepo,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }),
  });
}

export function useUploadZip() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ file, projectId, platform }: { file: File; projectId: string; platform?: string }) =>
      api.uploadZip(file, projectId, platform),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }),
  });
}

export function useScan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId, force }: { projectId: string; force?: boolean }) =>
      api.scan(projectId, force),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }),
  });
}

/** Starts the documentation generation workflow for a project. */
export function useGenerate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (options: {
      projectId: string;
      resume?: boolean;
      investigationModel?: string | null;
      model?: string | null;
      writeModel?: string | null;
      generationMethod?: "custom" | "opencode-cli" | "claude-cli" | null;
      generationProfile?: Record<string, unknown> | null;
      liveEvidence?: boolean;
    }): Promise<import("../types").StatusResponse> =>
      api.generate({ ...options, resume: options.resume ?? true }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["projects"] });
    },
  });
}

export function usePushToRag() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (projectId: string) => api.pushToRag(projectId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }),
  });
}

export function useResetStatus() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (projectId: string) => api.resetStatus(projectId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }),
  });
}

export function useRepairLatestGitUpdate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (projectId: string) => api.repairLatestGitUpdate(projectId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }),
  });
}

export function useDeleteProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (projectId: string) => api.deleteProject(projectId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }),
  });
}

export function useGenerateRuns(projectId: string | null) {
  return useQuery({
    queryKey: ["generate-runs", projectId],
    queryFn: () => api.listGenerateRuns(projectId!),
    enabled: !!projectId,
    refetchInterval: (query) => {
      const runs = query.state.data?.runs ?? [];
      return runs.some((r) => r.status === "pending" || r.status === "running") ? 10_000 : false;
    },
  });
}

/**
 * Historical log files for a project.
 *
 * NOT polled. A new file appears when a run finishes, and the log stream's
 * `done` event already invalidates this key in `App.tsx` -- which is exact,
 * unlike the blanket 5s poll it replaces (measured: 343 calls to
 * `/mcp/logs/{project}` in ~2h, because the log panel is mounted the whole
 * time an operator is on the Projects page).
 */
export function useLogFiles(projectId: string | null) {
  return useQuery({
    queryKey: ["log-files", projectId],
    queryFn: () => api.listLogFiles(projectId!),
    enabled: !!projectId,
    staleTime: RUN_ARTIFACT_STALE_MS,
  });
}

export function usePluginPacks() {
  return useQuery({
    queryKey: ["plugin-packs"],
    queryFn: api.listPluginPacks,
    // Catalogue. Register/pull/upload/delete all invalidate this key, so a poll
    // adds nothing but load (measured: 334 calls to `/mcp/plugins` in ~2h).
    staleTime: CATALOGUE_STALE_MS,
  });
}

export function useStarterPack() {
  return useQuery({
    queryKey: ["starter-pack"],
    queryFn: api.getStarterPack,
    staleTime: CATALOGUE_STALE_MS,
  });
}

export function usePluginPackBranches(repoUrl?: string | null) {
  return useQuery({
    queryKey: ["plugin-pack-branches", repoUrl ?? "starter"],
    queryFn: () => api.discoverPluginPackBranches(repoUrl),
    staleTime: 0,
    refetchOnMount: "always",
  });
}

export function useAssignPluginPack() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId, pluginPackId }: { projectId: string; pluginPackId: number | null }) =>
      api.assignPluginPack(projectId, pluginPackId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }),
  });
}

export function useRegisterPluginPack() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { path?: string; repo_url?: string; repo_branch?: string }) =>
      api.registerPluginPack(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["plugin-packs"] });
      qc.invalidateQueries({ queryKey: ["plugin-pack-branches"] });
    },
  });
}

export function useUploadPluginPackZip() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => api.uploadPluginPackZip(file),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["plugin-packs"] }),
  });
}

export function usePullPluginPack() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (packId: number) => api.pullPluginPack(packId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["plugin-packs"] });
      qc.invalidateQueries({ queryKey: ["plugin-pack-branches"] });
    },
  });
}

export function useDeletePluginPack() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (packId: number) => api.deletePluginPack(packId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["plugin-packs"] });
      qc.invalidateQueries({ queryKey: ["plugin-pack-branches"] });
      qc.invalidateQueries({ queryKey: ["projects"] });
    },
  });
}

export function usePluginPackFiles(packId: number | null) {
  return useQuery({
    queryKey: ["plugin-pack-files", packId],
    queryFn: () => api.getPluginPackFiles(packId!),
    enabled: packId !== null,
    staleTime: 5_000,
  });
}

export function usePluginPackFile(packId: number | null, filePath: string | null) {
  return useQuery({
    queryKey: ["plugin-pack-file", packId, filePath],
    queryFn: () => api.getPluginPackFile(packId!, filePath!),
    enabled: packId !== null && filePath !== null,
    staleTime: 0,
  });
}

export function usePluginPackBranchFiles(repoUrl: string | null, repoBranch: string | null) {
  return useQuery({
    queryKey: ["plugin-pack-branch-files", repoUrl, repoBranch],
    queryFn: () => api.getPluginPackBranchFiles(repoUrl!, repoBranch!),
    enabled: repoUrl !== null && repoBranch !== null,
    staleTime: 0,
  });
}

export function usePluginPackBranchFile(
  repoUrl: string | null,
  repoBranch: string | null,
  filePath: string | null,
) {
  return useQuery({
    queryKey: ["plugin-pack-branch-file", repoUrl, repoBranch, filePath],
    queryFn: () => api.getPluginPackBranchFile(repoUrl!, repoBranch!, filePath!),
    enabled: repoUrl !== null && repoBranch !== null && filePath !== null,
    staleTime: 0,
  });
}

export function useSavePluginPackFile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ packId, filePath, content }: { packId: number; filePath: string; content: string }) =>
      api.savePluginPackFile(packId, filePath, content),
    onSuccess: (_data, { packId, filePath }) => {
      qc.invalidateQueries({ queryKey: ["plugin-pack-file", packId, filePath] });
      qc.invalidateQueries({ queryKey: ["plugin-pack-files", packId] });
      qc.invalidateQueries({ queryKey: ["plugin-packs"] });
    },
  });
}

export function useScanRuns(projectId: string | null) {
  return useQuery({
    queryKey: ["scan-runs", projectId],
    queryFn: () => api.listScanRuns(projectId!),
    enabled: !!projectId,
    refetchInterval: (query) => {
      const runs = query.state.data?.runs ?? [];
      return runs.some((r) => r.status === "pending" || r.status === "running") ? 10_000 : false;
    },
  });
}

export function useScanStatus(projectId: string | null) {
  return useQuery({
    queryKey: ["scan-status", projectId],
    queryFn: () => api.getStatus(projectId!),
    enabled: !!projectId,
    refetchInterval: (query) => {
      return query.state.data?.status === "scanning" ? 10_000 : false;
    },
  });
}

/**
 * The document list, including each document's sub-project.
 *
 * NOT polled: the set of published documents changes only when a generation run
 * swaps `docs_staging/` into `docs/`, and the run-completion effect in `App.tsx`
 * invalidates this key. Staging is the one case an operator might watch mid-run,
 * and the run itself is already visibly polled through `useProjects`.
 */
export function useDocs(projectId: string | null, source: DocSource = "published", enabled = true) {
  return useQuery({
    queryKey: ["docs", projectId, source],
    queryFn: () => api.listDocs(projectId!, source),
    enabled: !!projectId && enabled,
    staleTime: RUN_ARTIFACT_STALE_MS,
  });
}

export function useUpdateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId, data }: { projectId: string; data: { display_name?: string; platform?: string; doc_generation_provider?: string | null; instruction_pack_dir?: string | null } }) =>
      api.updateProject(projectId, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }),
  });
}

/** Token/cost totals. Only a finished run moves these; invalidated on `done`. */
export function useProjectUsage(projectId: string | null) {
  return useQuery({
    queryKey: ["project-usage", projectId],
    queryFn: () => api.getProjectUsage(projectId!),
    enabled: !!projectId,
    staleTime: RUN_ARTIFACT_STALE_MS,
  });
}

/** One row per completed run; invalidated on `done`. */
export function useQualityHistory(projectId: string | null) {
  return useQuery({
    queryKey: ["quality-history", projectId],
    queryFn: () => api.getQualityHistory(projectId!),
    enabled: !!projectId,
    staleTime: RUN_ARTIFACT_STALE_MS,
  });
}

/** Immutable once its run exists -- it is that run's questionnaire. */
export function useQuestionnaire(projectId: string | null, runId: number | null) {
  return useQuery({
    queryKey: ["questionnaire", projectId, runId],
    queryFn: () => api.getQuestionnaire(projectId!, runId!),
    enabled: !!projectId && runId != null,
    staleTime: CATALOGUE_STALE_MS,
  });
}

/** Changes only when a human submits one, and `useSubmitReview` invalidates it. */
export function useReviews(projectId: string | null, runId?: number | null) {
  return useQuery({
    queryKey: ["reviews", projectId, runId ?? null],
    queryFn: () => api.getReviews(projectId!, runId),
    enabled: !!projectId,
    staleTime: RUN_ARTIFACT_STALE_MS,
  });
}

export function useSubmitReview() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      projectId,
      data,
    }: {
      projectId: string;
      data: Parameters<typeof api.submitReview>[1];
    }) => api.submitReview(projectId, data),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: ["reviews", variables.projectId] });
    },
  });
}

/** Instance-level metadata: changes on deploy, not during a session. */
export function useInstructionPack() {
  return useQuery({
    queryKey: ["instruction-pack"],
    queryFn: api.getInstructionPack,
    staleTime: CATALOGUE_STALE_MS,
  });
}

export function useRegisterWebhook() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (projectId: string) => api.registerWebhook(projectId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }),
  });
}

export function useUpdateGithubToken() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId, token }: { projectId: string; token: string }) =>
      api.updateGithubToken(projectId, token),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }),
  });
}
