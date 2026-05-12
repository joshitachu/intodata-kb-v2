import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";

export function useProjects() {
  return useQuery({
    queryKey: ["projects"],
    queryFn: api.listProjects,
    refetchInterval: 10_000,
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
      templateProfileId,
      resume = true,
      model,
      writeModel,
    }: {
      projectId: string;
      templateProfileId?: number;
      resume?: boolean;
      model?: string | null;
      writeModel?: string | null;
    }) => api.generate(projectId, templateProfileId, resume, model, writeModel),
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
    refetchInterval: 10_000,
  });
}

export function useLogFiles(projectId: string | null) {
  return useQuery({
    queryKey: ["log-files", projectId],
    queryFn: () => api.listLogFiles(projectId!),
    enabled: !!projectId,
    refetchInterval: 10_000,
  });
}

export function useScanRuns(projectId: string | null) {
  return useQuery({
    queryKey: ["scan-runs", projectId],
    queryFn: () => api.listScanRuns(projectId!),
    enabled: !!projectId,
    refetchInterval: 10_000,
  });
}

export function useScanStatus(projectId: string | null) {
  return useQuery({
    queryKey: ["scan-status", projectId],
    queryFn: () => api.getStatus(projectId!),
    enabled: !!projectId,
    refetchInterval: 10_000,
  });
}

export function useDocs(projectId: string | null) {
  return useQuery({
    queryKey: ["docs", projectId],
    queryFn: () => api.listDocs(projectId!),
    enabled: !!projectId,
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
    refetchInterval: 10_000,
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
