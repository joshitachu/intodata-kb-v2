"use client";

import { useEffect, useState } from "react";
import { useCompleteOnboarding } from "@/hooks/useOnboarding";
import { useCompanyStatus } from "@/hooks/useSystemConfig";
import { useOnboardingContext } from "@/contexts/OnboardingContext";
import { useAuthContext } from "@/components/AuthProvider";
import { trackWizardEvent } from "@/lib/telemetry";
import { WizardProgress } from "./WizardProgress";
import { CapabilityStep } from "./steps/CapabilityStep";
import { GroundingStep } from "./steps/GroundingStep";
import { MemberReadyStep } from "./steps/MemberReadyStep";
import { MemberWelcomeStep } from "./steps/MemberWelcomeStep";

interface Props {
  onDismiss: () => void;
}

const STEP_LABELS = ["Welcome", "Your access", "How it works", "Ready"];

/**
 * First-run walkthrough for anyone who did not set up the deployment.
 *
 * Replaces a single card that said "your workspace is ready" and nothing else.
 * The four screens answer the questions a new member actually has — who let me
 * in, what am I allowed to do, can I trust the answers, what do I do first —
 * and hand off to the role-aware tour rather than dropping the user on an empty
 * chat page.
 *
 * Applies to admins too: an Admin who is not the deployment Owner has no
 * provisioning to do but very much needs to be told what their authority is.
 */
export function UserWelcomeWizard({ onDismiss }: Props) {
  const { user } = useAuthContext();
  const { data: companyStatus } = useCompanyStatus();
  const { status, startTour } = useOnboardingContext();
  const complete = useCompleteOnboarding();

  const [step, setStep] = useState(0);

  useEffect(() => {
    trackWizardEvent("wizard_step_viewed", step);
  }, [step]);

  const persona = status?.persona ?? "developer";
  // The member wizard has no workspace step of its own, so the name comes from
  // the company setup the admin already completed.
  const workspaceName = companyStatus?.workspace_name ?? status?.workspace_name;

  /**
   * Mark onboarding done, then leave. Dismissal happens regardless of whether
   * the write succeeds: a failed status update must not trap someone behind a
   * wizard they have finished.
   *
   * The failure is swallowed deliberately rather than rethrown. Rethrowing from
   * a `finally` would surface as an unhandled rejection at the call site, and
   * there is nothing useful to tell the user — the wizard reappearing on next
   * login is the whole consequence.
   */
  async function finish(startWalkthrough: boolean) {
    try {
      await complete.mutateAsync();
    } catch (error) {
      console.error("Failed to record onboarding completion", error);
    }
    trackWizardEvent(
      startWalkthrough ? "tour_started" : "tour_skipped",
      STEP_LABELS.length - 1,
    );
    onDismiss();
    if (startWalkthrough) startTour();
  }

  return (
    <div className="fixed inset-0 z-50 flex flex-col overflow-y-auto bg-background">
      {/* Ambient brand glow, matching the admin wizard. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-[45%] opacity-[0.06] dark:opacity-[0.12]"
        style={{
          background:
            "radial-gradient(55% 60% at 50% 0%, hsl(var(--primary)) 0%, transparent 72%)",
        }}
      />

      <WizardProgress
        steps={STEP_LABELS}
        currentStep={step}
        workspaceName={workspaceName}
      />

      <div
        key={step}
        className="animate-hero-enter relative flex flex-1 items-center justify-center py-10"
      >
        {step === 0 && (
          <MemberWelcomeStep
            username={user?.username ?? "there"}
            workspaceName={workspaceName}
            persona={persona}
            invite={status?.invite_context}
            onNext={() => setStep(1)}
          />
        )}
        {step === 1 && (
          <CapabilityStep
            persona={persona}
            onBack={() => setStep(0)}
            onNext={() => setStep(2)}
          />
        )}
        {step === 2 && (
          <GroundingStep onBack={() => setStep(1)} onNext={() => setStep(3)} />
        )}
        {step === 3 && (
          <MemberReadyStep
            persona={persona}
            isSaving={complete.isPending}
            onBack={() => setStep(2)}
            onStartTour={() => void finish(true)}
            onSkipTour={() => void finish(false)}
          />
        )}
      </div>
    </div>
  );
}
