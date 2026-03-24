import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";

export function useTemplates(params?: { category?: string; platform?: string }) {
  return useQuery({
    queryKey: ["templates", params?.category, params?.platform],
    queryFn: () => api.listTemplates(params),
    staleTime: 30_000,
  });
}

export function useTemplate(slug: string | null) {
  return useQuery({
    queryKey: ["template", slug],
    queryFn: () => api.getTemplate(slug!),
    enabled: !!slug,
  });
}

export function useVersions(slug: string | null) {
  return useQuery({
    queryKey: ["template-versions", slug],
    queryFn: () => api.listVersions(slug!),
    enabled: !!slug,
  });
}

export function useVersion(slug: string | null, versionNumber: number | null) {
  return useQuery({
    queryKey: ["template-version", slug, versionNumber],
    queryFn: () => api.getVersion(slug!, versionNumber!),
    enabled: !!slug && versionNumber != null,
  });
}

export function useCreateVersion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ slug, content, changeSummary, createdBy }: {
      slug: string;
      content: string;
      changeSummary: string;
      createdBy?: string;
    }) => api.createVersion(slug, {
      content,
      change_summary: changeSummary,
      created_by: createdBy,
    }),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["template-versions", vars.slug] });
      qc.invalidateQueries({ queryKey: ["templates"] });
    },
  });
}

export function useDiff(slug: string | null, from: number | null, to: number | null) {
  return useQuery({
    queryKey: ["template-diff", slug, from, to],
    queryFn: () => api.diffVersions(slug!, from!, to!),
    enabled: !!slug && from != null && to != null && from !== to,
  });
}

// --- Profiles ---

export function useProfiles(params?: { platform?: string; scope?: string }) {
  return useQuery({
    queryKey: ["profiles", params?.platform, params?.scope],
    queryFn: () => api.listProfiles(params),
    staleTime: 30_000,
  });
}

export function useProfile(id: number | null) {
  return useQuery({
    queryKey: ["profile", id],
    queryFn: () => api.getProfile(id!),
    enabled: id != null,
  });
}

export function useCreateProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { name: string; platform?: string; description?: string; scope?: string }) =>
      api.createProfile(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["profiles"] });
    },
  });
}

export function useCloneProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, newName }: { id: number; newName: string }) =>
      api.cloneProfile(id, newName),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["profiles"] });
    },
  });
}

export function useSetDefaultProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.setDefaultProfile(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["profiles"] });
    },
  });
}

export function useSetAssignment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ profileId, slug, versionId }: { profileId: number; slug: string; versionId: number }) =>
      api.setAssignment(profileId, slug, versionId),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["profile", vars.profileId] });
    },
  });
}
