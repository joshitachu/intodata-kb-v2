"use client";

import {
  createContext,
  useCallback,
  useContext,
  useState,
  type ReactNode,
} from "react";
import { useOnboardingStatus } from "@/hooks/useOnboarding";
import { useCompanyStatus } from "@/hooks/useSystemConfig";
import type { CompanyStatus, OnboardingStatus } from "@/types/onboarding";

interface OnboardingContextValue {
  status: OnboardingStatus | null;
  isLoading: boolean;
  isError: boolean;
  refetch: () => void;
  companyStatus: CompanyStatus | null;
  // Tour control (allows "Replay Tour" button to trigger from anywhere)
  tourActive: boolean;
  startTour: () => void;
  stopTour: () => void;
  // Wizard-local ZIP queue: File objects cannot be persisted via the onboarding
  // DB row (they don't JSON-serialize) so we stash them in memory keyed by
  // platform id. Lost on refresh — user is expected to re-select if they
  // abandon the wizard mid-upload.
  zipFiles: Record<string, File>;
  setZipFile: (platformId: string, file: File | null) => void;
  clearZipFiles: () => void;
}

const OnboardingContext = createContext<OnboardingContextValue>({
  status: null,
  isLoading: false,
  isError: false,
  refetch: () => {},
  companyStatus: null,
  tourActive: false,
  startTour: () => {},
  stopTour: () => {},
  zipFiles: {},
  setZipFile: () => {},
  clearZipFiles: () => {},
});

export function OnboardingProvider({ children }: { children: ReactNode }) {
  const { data, isLoading, isError, refetch } = useOnboardingStatus();
  const { data: companyData } = useCompanyStatus();
  const [tourActive, setTourActive] = useState(false);
  const [zipFiles, setZipFiles] = useState<Record<string, File>>({});

  const setZipFile = useCallback((platformId: string, file: File | null) => {
    setZipFiles((prev) => {
      const next = { ...prev };
      if (file) next[platformId] = file;
      else delete next[platformId];
      return next;
    });
  }, []);

  const clearZipFiles = useCallback(() => setZipFiles({}), []);

  return (
    <OnboardingContext.Provider
      value={{
        status: data ?? null,
        isLoading,
        isError,
        refetch: () => refetch(),
        companyStatus: companyData ?? null,
        tourActive,
        startTour: () => setTourActive(true),
        stopTour: () => setTourActive(false),
        zipFiles,
        setZipFile,
        clearZipFiles,
      }}
    >
      {children}
    </OnboardingContext.Provider>
  );
}

export function useOnboardingContext() {
  return useContext(OnboardingContext);
}
