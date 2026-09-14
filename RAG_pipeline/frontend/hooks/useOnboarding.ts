"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { authedFetch, fetchJson } from "@/lib/authedFetch";
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
  // Routed through `fetchJson` so a 401 in the stale-access-token window on a
  // cold load is refreshed and replayed. It used to throw, and the resulting
  // retry backoff held `isLoading` true for ~7s — during which
  // `OnboardingGuard` rendered nothing at all and the whole app was blank.
  return OnboardingStatusSchema.parse(
    await fetchJson<unknown>("/api/onboarding/status", {
      cache: "no-store",
      fallbackError: "Failed to fetch onboarding status",
    }),
  );
}

async function putStep<T>(step: string, body: T): Promise<OnboardingStatus> {
  return OnboardingStatusSchema.parse(
    await fetchJson<unknown>(`/api/onboarding/step/${step}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      fallbackError: `Failed to save ${step}`,
    }),
  );
}

async function postComplete(): Promise<CompleteWizardResponse> {
  return CompleteWizardResponseSchema.parse(
    await fetchJson<unknown>("/api/onboarding/complete", {
      method: "POST",
      fallbackError: "Failed to complete wizard",
    }),
  );
}

async function fetchSetupStatus(): Promise<SetupStatusResponse> {
  return SetupStatusResponseSchema.parse(
    await fetchJson<unknown>("/api/onboarding/setup-status", {
      cache: "no-store",
      fallbackError: "Failed to fetch setup status",
    }),
  );
}

async function postTourComplete(variant?: string): Promise<OnboardingStatus> {
  return OnboardingStatusSchema.parse(
    await fetchJson<unknown>("/api/onboarding/tour/complete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ variant: variant ?? null }),
      fallbackError: "Failed to complete tour",
    }),
  );
}

export interface TourStepProgress {
  tour_step: number;
  /** Stable step id — what actually makes the tour resumable per persona. */
  step_id?: string;
  variant?: string;
}

async function putTourStep(progress: TourStepProgress): Promise<OnboardingStatus> {
  return OnboardingStatusSchema.parse(
    await fetchJson<unknown>("/api/onboarding/tour/step", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(progress),
      fallbackError: "Failed to update tour step",
    }),
  );
}

export function useOnboardingStatus() {
  return useQuery({
    queryKey: ["onboarding", "status"],
    queryFn: fetchStatus,
    staleTime: 30 * 1000,
  });
}

async function fetchConfig(): Promise<OnboardingConfig> {
  // Keeps the never-block fallback below, but a 401 now rotates and replays
  // first — otherwise a cold load inside the stale-token window silently
  // pinned the wizard to the hardcoded platform list for the whole session.
  const res = await authedFetch("/api/onboarding/config", { cache: "no-store" });
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
