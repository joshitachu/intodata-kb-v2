"use client";

import {
  createContext,
  useContext,
  useState,
  type ReactNode,
} from "react";
import { useOnboardingStatus } from "@/hooks/useOnboarding";
import type { OnboardingStatus } from "@/types/onboarding";

interface OnboardingContextValue {
  status: OnboardingStatus | null;
  isLoading: boolean;
  isError: boolean;
  refetch: () => void;
  // Tour control (allows "Replay Tour" button to trigger from anywhere)
  tourActive: boolean;
  startTour: () => void;
  stopTour: () => void;
}

const OnboardingContext = createContext<OnboardingContextValue>({
  status: null,
  isLoading: false,
  isError: false,
  refetch: () => {},
  tourActive: false,
  startTour: () => {},
  stopTour: () => {},
});

export function OnboardingProvider({ children }: { children: ReactNode }) {
  const { data, isLoading, isError, refetch } = useOnboardingStatus();
  const [tourActive, setTourActive] = useState(false);

  return (
    <OnboardingContext.Provider
      value={{
        status: data ?? null,
        isLoading,
        isError,
        refetch: () => refetch(),
        tourActive,
        startTour: () => setTourActive(true),
        stopTour: () => setTourActive(false),
      }}
    >
      {children}
    </OnboardingContext.Provider>
  );
}

export function useOnboardingContext() {
  return useContext(OnboardingContext);
}
