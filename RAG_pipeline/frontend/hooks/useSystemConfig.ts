"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchJson } from "@/lib/authedFetch";
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
  return CompanyStatusSchema.parse(
    await fetchJson<unknown>("/api/onboarding/company/status", {
      cache: "no-store",
      fallbackError: "Failed to fetch company onboarding status",
    }),
  );
}

async function putCompanyStep<T>(step: string, body: T): Promise<CompanyStatus> {
  // `fetchJson` already unwraps `error` / `detail` / `detail.message` from the
  // response body, so the manual `detail` read this used to do is gone.
  return CompanyStatusSchema.parse(
    await fetchJson<unknown>(`/api/onboarding/company/step/${step}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      fallbackError: `Failed to save ${step}`,
    }),
  );
}

async function postCompanyComplete(): Promise<CompleteCompanyWizardResponse> {
  return CompleteCompanyWizardResponseSchema.parse(
    await fetchJson<unknown>("/api/onboarding/company/complete", {
      method: "POST",
      fallbackError: "Failed to complete company wizard",
    }),
  );
}

async function fetchCompanySetupStatus(): Promise<SetupStatusResponse> {
  return SetupStatusResponseSchema.parse(
    await fetchJson<unknown>("/api/onboarding/company/setup-status", {
      cache: "no-store",
      fallbackError: "Failed to fetch company setup status",
    }),
  );
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
