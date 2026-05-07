"use client";

import { usePathname } from "next/navigation";
import { useRef, useState, type ReactNode } from "react";
import { useAuthContext } from "@/components/AuthProvider";
import { useOnboardingContext } from "@/contexts/OnboardingContext";
import { OnboardingWizard } from "./OnboardingWizard";

const SKIP_PATHS = ["/login"];

interface Props {
  children: ReactNode;
}

/**
 * Wraps the app and mounts the onboarding wizard over the top for users whose
 * wizard is incomplete.
 *
 * Once a wizard session is started we keep it mounted until the user explicitly
 * dismisses it (from the Ready step). Otherwise the Guard would unmount the
 * wizard the moment `complete_wizard` flips `wizard_completed=true`, which
 * previously caused the wizard to vanish behind the auto-starting product tour.
 */
export function OnboardingGuard({ children }: Props) {
  const pathname = usePathname();
  const { user, isLoading: authLoading } = useAuthContext();
  const { status, isLoading: onboardingLoading } = useOnboardingContext();

  const sessionStarted = useRef(false);
  const [sessionDismissed, setSessionDismissed] = useState(false);

  if (SKIP_PATHS.some((p) => pathname.startsWith(p))) return <>{children}</>;
  if (authLoading || !user) return <>{children}</>;
  if (onboardingLoading) return <>{children}</>;

  // `status.wizard_completed` is the source of truth. `user.onboarding_completed`
  // is captured at login and never updates, so we only use it as a hint while
  // status is still loading (above).
  const wizardDone = status
    ? status.wizard_completed
    : user.onboarding_completed;

  // Latch the session on the first render where the wizard is not done.
  // Once latched, the session stays open until the user dismisses it —
  // even if `wizard_completed` flips true mid-flow (it does, as soon as
  // the user reaches the Setup step).
  if (!wizardDone && !sessionStarted.current) {
    sessionStarted.current = true;
  }

  if (sessionStarted.current && !sessionDismissed) {
    return (
      <>
        {children}
        <OnboardingWizard onDismiss={() => setSessionDismissed(true)} />
      </>
    );
  }

  return <>{children}</>;
}
