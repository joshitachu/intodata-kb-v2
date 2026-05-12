"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CompanyStatus,
  CompanyStatusSchema,
  CompleteCompanyWizardResponse,
  CompleteCompanyWizardResponseSchema,
  SetupStatusResponse,
  SetupStatusResponseSchema,
  SourceType,
} from "@/types/onboarding";

async function fetchCompanyStatus(): Promise<CompanyStatus> {
  const res = await fetch("/api/onboarding/company/status", { cache: "no-store" });
  if (!res.ok) throw new Error("Failed to fetch company onboarding status");
  return CompanyStatusSchema.parse(await res.json());
}

async function putCompanyStep<T>(step: string, body: T): Promise<CompanyStatus> {
  const res = await fetch(`/api/onboarding/company/step/${step}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error((data as { detail?: string }).detail || `Failed to save ${step}`);
  }
  return CompanyStatusSchema.parse(await res.json());
}

async function postCompanyComplete(): Promise<CompleteCompanyWizardResponse> {
  const res = await fetch("/api/onboarding/company/complete", { method: "POST" });
  if (!res.ok) throw new Error("Failed to complete company wizard");
  return CompleteCompanyWizardResponseSchema.parse(await res.json());
}

async function fetchCompanySetupStatus(): Promise<SetupStatusResponse> {
  const res = await fetch("/api/onboarding/company/setup-status", { cache: "no-store" });
  if (!res.ok) throw new Error("Failed to fetch company setup status");
  return SetupStatusResponseSchema.parse(await res.json());
}

export function useCompanyStatus() {
  return useQuery({
    queryKey: ["onboarding", "company", "status"],
    queryFn: fetchCompanyStatus,
    staleTime: 30 * 1000,
  });
}

export function useSaveCompanyWorkspace() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (workspace_name: string) =>
      putCompanyStep("workspace", { workspace_name }),
    onSuccess: (data) =>
      qc.setQueryData(["onboarding", "company", "status"], data),
  });
}

export function useSaveCompanyPlatforms() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (platforms: string[]) =>
      putCompanyStep("platforms", { platforms }),
    onSuccess: (data) =>
      qc.setQueryData(["onboarding", "company", "status"], data),
  });
}

export function useSaveCompanyCommTools() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (comm_tools: string[]) =>
      putCompanyStep("comm_tools", { comm_tools }),
    onSuccess: (data) =>
      qc.setQueryData(["onboarding", "company", "status"], data),
  });
}

export function useSaveCompanySource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      source_type: SourceType;
      source_url?: string | null;
      source_filename?: string | null;
    }) => putCompanyStep("source", body),
    onSuccess: (data) =>
      qc.setQueryData(["onboarding", "company", "status"], data),
  });
}

export function useCompleteCompanyOnboarding() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: postCompanyComplete,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["onboarding", "company", "status"] });
      qc.invalidateQueries({ queryKey: ["onboarding", "status"] });
      qc.invalidateQueries({ queryKey: ["auth", "me"] });
    },
  });
}

export function useCompanySetupStatusPolling(enabled: boolean) {
  return useQuery({
    queryKey: ["onboarding", "company", "setup-status"],
    queryFn: fetchCompanySetupStatus,
    enabled,
    refetchInterval: enabled ? 3000 : false,
    staleTime: 0,
  });
}
