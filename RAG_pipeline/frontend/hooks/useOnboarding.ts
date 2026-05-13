"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiErrorMessage } from "@/lib/apiError";
import {
  CommTool,
  CompleteWizardResponse,
  CompleteWizardResponseSchema,
  FALLBACK_CONFIG,
  OnboardingConfig,
  OnboardingConfigSchema,
  OnboardingStatus,
  OnboardingStatusSchema,
  Platform,
  SetupStatusResponse,
  SetupStatusResponseSchema,
  SourceType,
} from "@/types/onboarding";

async function fetchStatus(): Promise<OnboardingStatus> {
  const res = await fetch("/api/onboarding/status", { cache: "no-store" });
  if (!res.ok) {
    throw new Error("Failed to fetch onboarding status");
  }
  return OnboardingStatusSchema.parse(await res.json());
}

async function putStep<T>(step: string, body: T): Promise<OnboardingStatus> {
  const res = await fetch(`/api/onboarding/step/${step}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(apiErrorMessage(data.detail, `Failed to save ${step}`));
  }
  return OnboardingStatusSchema.parse(await res.json());
}

async function postComplete(): Promise<CompleteWizardResponse> {
  const res = await fetch("/api/onboarding/complete", { method: "POST" });
  if (!res.ok) {
    throw new Error("Failed to complete wizard");
  }
  return CompleteWizardResponseSchema.parse(await res.json());
}

async function fetchSetupStatus(): Promise<SetupStatusResponse> {
  const res = await fetch("/api/onboarding/setup-status", { cache: "no-store" });
  if (!res.ok) {
    throw new Error("Failed to fetch setup status");
  }
  return SetupStatusResponseSchema.parse(await res.json());
}

async function postTourComplete(): Promise<OnboardingStatus> {
  const res = await fetch("/api/onboarding/tour/complete", { method: "POST" });
  if (!res.ok) {
    throw new Error("Failed to complete tour");
  }
  return OnboardingStatusSchema.parse(await res.json());
}

async function putTourStep(tour_step: number): Promise<OnboardingStatus> {
  const res = await fetch("/api/onboarding/tour/step", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tour_step }),
  });
  if (!res.ok) {
    throw new Error("Failed to update tour step");
  }
  return OnboardingStatusSchema.parse(await res.json());
}

export function useOnboardingStatus() {
  return useQuery({
    queryKey: ["onboarding", "status"],
    queryFn: fetchStatus,
    staleTime: 30 * 1000,
  });
}

async function fetchConfig(): Promise<OnboardingConfig> {
  const res = await fetch("/api/onboarding/config", { cache: "no-store" });
  if (!res.ok) {
    // Let the wizard keep working with the fallback list — don't block UX
    // on a config fetch failure.
    return FALLBACK_CONFIG;
  }
  try {
    return OnboardingConfigSchema.parse(await res.json());
  } catch {
    return FALLBACK_CONFIG;
  }
}

export function useOnboardingConfig() {
  return useQuery({
    queryKey: ["onboarding", "config"],
    queryFn: fetchConfig,
    staleTime: Infinity, // Config is process-lifetime — no point refetching
    initialData: FALLBACK_CONFIG,
  });
}

export function useSaveWorkspace() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (workspace_name: string) =>
      putStep("workspace", { workspace_name }),
    onSuccess: (data) =>
      qc.setQueryData(["onboarding", "status"], data),
  });
}

export function useSavePlatforms() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (platforms: Platform[]) => putStep("platforms", { platforms }),
    onSuccess: (data) =>
      qc.setQueryData(["onboarding", "status"], data),
  });
}

export function useSaveCommTools() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (comm_tools: CommTool[]) =>
      putStep("comm_tools", { comm_tools }),
    onSuccess: (data) =>
      qc.setQueryData(["onboarding", "status"], data),
  });
}

export function useSaveSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      source_type: SourceType;
      source_url?: string | null;
      source_filename?: string | null;
    }) => putStep("source", body),
    onSuccess: (data) =>
      qc.setQueryData(["onboarding", "status"], data),
  });
}

export function useCompleteOnboarding() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: postComplete,
    onSuccess: (data) => {
      qc.setQueryData<OnboardingStatus | undefined>(
        ["onboarding", "status"],
        (prev) =>
          prev
            ? {
                ...prev,
                wizard_completed: data.wizard_completed,
                setup_status: data.setup_status,
              }
            : prev,
      );
      qc.invalidateQueries({ queryKey: ["onboarding", "status"] });
      qc.invalidateQueries({ queryKey: ["auth", "me"] });
    },
  });
}

export function useSetupStatusPolling(enabled: boolean) {
  return useQuery({
    queryKey: ["onboarding", "setup-status"],
    queryFn: fetchSetupStatus,
    enabled,
    refetchInterval: enabled ? 3000 : false,
    staleTime: 0,
  });
}

export function useCompleteTour() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: postTourComplete,
    onSuccess: (data) => {
      qc.setQueryData(["onboarding", "status"], data);
      qc.invalidateQueries({ queryKey: ["auth", "me"] });
    },
  });
}

export function useSaveTourStep() {
  return useMutation({
    mutationFn: putTourStep,
  });
}
