"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { useAuthContext } from "@/components/AuthProvider";
import { useOnboardingContext } from "@/contexts/OnboardingContext";
import { OnboardingWizard } from "./OnboardingWizard";

const SKIP_PATHS = ["/login"];

interface Props {
  children: ReactNode;
}

/**
 * Wraps the app and mounts the onboarding wizard over the top for users whose
 * wizard is incomplete. Existing users pass through untouched.
 */
export function OnboardingGuard({ children }: Props) {
  const pathname = usePathname();
  const { user, isLoading: authLoading } = useAuthContext();
  const { status, isLoading: onboardingLoading } = useOnboardingContext();

  // Never show wizard on login or when we don't know auth state yet
  if (SKIP_PATHS.some((p) => pathname.startsWith(p))) return <>{children}</>;
  if (authLoading || !user) return <>{children}</>;
  if (onboardingLoading) return <>{children}</>;

  // `status.wizard_completed` is the source of truth — it's fresh after the
  // wizard's `complete` mutation invalidates and refetches. `user.onboarding_completed`
  // is captured at login and never updates, so we only use it as a hint while
  // status is still loading (above).
  const wizardDone = status
    ? status.wizard_completed
    : user.onboarding_completed;

  if (!wizardDone) {
    return (
      <>
        {children}
        <OnboardingWizard />
      </>
    );
  }

  return <>{children}</>;
}
