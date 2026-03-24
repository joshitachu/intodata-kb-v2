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
    mutationFn: ({ projectId, templateProfileId }: { projectId: string; templateProfileId?: number }) =>
      api.generate(projectId, templateProfileId),
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
    mutationFn: ({ projectId, data }: { projectId: string; data: { display_name?: string; platform?: string } }) =>
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
