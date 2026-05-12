"use client";

import { usePathname } from "next/navigation";
import { useRef, useState, type ReactNode } from "react";
import { useAuthContext } from "@/components/AuthProvider";
import { useOnboardingContext } from "@/contexts/OnboardingContext";
import { AdminOnboardingWizard } from "./AdminOnboardingWizard";
import { UserWelcomeWizard } from "./UserWelcomeWizard";

const SKIP_PATHS = ["/login"];

interface Props {
  children: ReactNode;
}

/**
 * Mounts the appropriate wizard over the app:
 * - Superadmin with no company config done → AdminOnboardingWizard (full 7-step)
 * - Regular user with no personal onboarding done → UserWelcomeWizard (slim welcome)
 * - Everyone else → nothing
 *
 * Once a session is latched it stays mounted until the user explicitly dismisses
 * it, preventing the wizard from vanishing when wizard_completed flips mid-flow.
 */
export function OnboardingGuard({ children }: Props) {
  const pathname = usePathname();
  const { user, isLoading: authLoading } = useAuthContext();
  const { status, isLoading: onboardingLoading } = useOnboardingContext();

  const sessionStarted = useRef<"admin" | "user" | null>(null);
  const [sessionDismissed, setSessionDismissed] = useState(false);

  if (SKIP_PATHS.some((p) => pathname.startsWith(p))) return <>{children}</>;
  if (authLoading || !user) return <>{children}</>;
  if (onboardingLoading) return null;

  const companyDone = status?.company_wizard_completed ?? false;
  const userDone = status ? status.wizard_completed : user.onboarding_completed;

  // Latch: once a wizard session starts, remember which type it is and keep it
  // open until the user dismisses — even after the completed flag flips.
  if (!sessionStarted.current && !sessionDismissed) {
    if (user.is_superadmin && !companyDone) {
      sessionStarted.current = "admin";
    } else if (!user.is_superadmin && !userDone) {
      sessionStarted.current = "user";
    }
  }

  const dismiss = () => setSessionDismissed(true);

  if (sessionStarted.current && !sessionDismissed) {
    return (
      <>
        {children}
        {sessionStarted.current === "admin" ? (
          <AdminOnboardingWizard onDismiss={dismiss} />
        ) : (
          <UserWelcomeWizard onDismiss={dismiss} />
        )}
      </>
    );
  }

  return <>{children}</>;
}
