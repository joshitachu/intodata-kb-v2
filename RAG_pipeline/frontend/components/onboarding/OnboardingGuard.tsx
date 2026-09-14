"use client";

import { usePathname } from "next/navigation";
import { useRef, useState, type ReactNode } from "react";
import { useAuthContext } from "@/components/AuthProvider";
import { useOnboardingContext } from "@/contexts/OnboardingContext";
import { AdminOnboardingWizard } from "./AdminOnboardingWizard";
import { UserWelcomeWizard } from "./UserWelcomeWizard";

/**
 * Paths that must never be covered by a wizard.
 *
 * Security setup and profile completion are themselves mandatory first-run
 * steps: overlaying the onboarding wizard on top would hide the password or MFA
 * form the user was just sent to, with no way to reach it.
 */
const SKIP_PATHS = ["/login", "/onboarding", "/settings/security", "/guide"];

interface Props {
  children: ReactNode;
}

/**
 * Mounts the right first-run wizard over the app.
 *
 * - Deployment setup not done, and this user can do it → provisioning wizard.
 * - Personal onboarding not done → member walkthrough (persona-aware content).
 * - Otherwise → nothing.
 *
 * Previously this branched on `is_superadmin` alone, which meant an Admin who
 * was not the deployment Owner fell into the "everyone else" branch and got the
 * one-card welcome. Persona comes from the backend now, so the decision matches
 * the user's real authority.
 *
 * Once a session is latched it stays mounted until the user explicitly
 * dismisses it, so the wizard does not vanish mid-flow when a completion flag
 * flips underneath it.
 */
export function OnboardingGuard({ children }: Props) {
  const pathname = usePathname();
  const { user, isLoading: authLoading } = useAuthContext();
  const { status, isLoading: onboardingLoading, isError } = useOnboardingContext();

  const sessionStarted = useRef<"admin" | "user" | null>(null);
  const [sessionDismissed, setSessionDismissed] = useState(false);

  if (SKIP_PATHS.some((p) => pathname.startsWith(p))) return <>{children}</>;
  if (authLoading || !user) return <>{children}</>;
  // Render the app rather than a blank screen when status cannot be fetched.
  // Blocking on a failed request would lock the user out of a working product
  // over an onboarding-flag lookup.
  if (isError) return <>{children}</>;

  const companyDone = status?.company_wizard_completed ?? false;
  const userDone = status ? status.wizard_completed : user.onboarding_completed;
  // Only whoever can actually provision the deployment should be handed the
  // provisioning wizard. An admin joining an already-configured workspace gets
  // the member walkthrough, not a setup flow they have no reason to run.
  const canProvision = status?.persona === "owner" || user.is_superadmin;

  // While the status lookup is in flight the app renders and NO wizard
  // decision is made. Two separate reasons, and both matter:
  //
  //  - This gate used to `return null`, unmounting the entire application for
  //    the duration of an onboarding-flag lookup. On a cold load /auth/me and
  //    /onboarding/status race: the moment `user` lands while status is still
  //    pending, the whole tree blanked and then remounted, losing page-local
  //    state (app/docs/page.tsx `selectedDoc` reset to null). If the status
  //    request failed, `isLoading` stayed true across the retry backoff and
  //    the app was blank for seconds before the isError branch let it through.
  //  - Deciding early would be worse than blanking: `sessionStarted` is a ref,
  //    so latching against unknown flags (`companyDone` defaulting to false)
  //    would pin the provisioning wizard for the rest of the session on a
  //    deployment that is already configured. The decision waits; the app
  //    does not. The wizard mounts over the app when the flags arrive.
  if (!onboardingLoading && !sessionStarted.current && !sessionDismissed) {
    if (canProvision && !companyDone) {
      sessionStarted.current = "admin";
    } else if (!userDone) {
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
