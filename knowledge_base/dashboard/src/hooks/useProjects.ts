import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { DocSource } from "../types";

export function useProjects() {
  return useQuery({
    queryKey: ["projects"],
    queryFn: api.listProjects,
    refetchInterval: (query) => {
      const projects = query.state.data?.projects ?? [];
      return projects.some((p) => p.status === "scanning" || p.status === "generating") ? 10_000 : false;
    },
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

export function useGenerate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      projectId,
      resume = true,
      investigationModel,
      model,
      writeModel,
    }: {
      projectId: string;
      resume?: boolean;
      investigationModel?: string | null;
      model?: string | null;
      writeModel?: string | null;
    }) => api.generate(projectId, resume, investigationModel, model, writeModel),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }),
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

export function useLogFiles(projectId: string | null) {
  return useQuery({
    queryKey: ["log-files", projectId],
    queryFn: () => api.listLogFiles(projectId!),
    enabled: !!projectId,
  });
}

export function usePluginPacks() {
  return useQuery({
    queryKey: ["plugin-packs"],
    queryFn: api.listPluginPacks,
    staleTime: 30_000,
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
    mutationFn: (body: { path?: string; repo_url?: string }) => api.registerPluginPack(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["plugin-packs"] }),
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
    onSuccess: () => qc.invalidateQueries({ queryKey: ["plugin-packs"] }),
  });
}

export function useDeletePluginPack() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (packId: number) => api.deletePluginPack(packId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["plugin-packs"] });
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

export function useDocs(projectId: string | null, source: DocSource = "published", enabled = true) {
  return useQuery({
    queryKey: ["docs", projectId, source],
    queryFn: () => api.listDocs(projectId!, source),
    enabled: !!projectId && enabled,
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

export function useProjectUsage(projectId: string | null) {
  return useQuery({
    queryKey: ["project-usage", projectId],
    queryFn: () => api.getProjectUsage(projectId!),
    enabled: !!projectId,
  });
}

export function useQualityHistory(projectId: string | null) {
  return useQuery({
    queryKey: ["quality-history", projectId],
    queryFn: () => api.getQualityHistory(projectId!),
    enabled: !!projectId,
  });
}

export function useQuestionnaire(projectId: string | null, runId: number | null) {
  return useQuery({
    queryKey: ["questionnaire", projectId, runId],
    queryFn: () => api.getQuestionnaire(projectId!, runId!),
    enabled: !!projectId && runId != null,
  });
}

export function useReviews(projectId: string | null, runId?: number | null) {
  return useQuery({
    queryKey: ["reviews", projectId, runId ?? null],
    queryFn: () => api.getReviews(projectId!, runId),
    enabled: !!projectId,
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

export function useInstructionPack() {
  return useQuery({
    queryKey: ["instruction-pack"],
    queryFn: api.getInstructionPack,
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
