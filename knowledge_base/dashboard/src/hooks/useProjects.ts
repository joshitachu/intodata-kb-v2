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
    mutationFn: (projectId: string) => api.generate(projectId),
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
