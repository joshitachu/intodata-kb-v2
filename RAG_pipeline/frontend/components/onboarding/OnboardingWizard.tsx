"use client";

import { useEffect, useState } from "react";
import { WizardProgress } from "./WizardProgress";
import { WelcomeStep } from "./steps/WelcomeStep";
import { WorkspaceStep } from "./steps/WorkspaceStep";
import { PlatformSelectStep } from "./steps/PlatformSelectStep";
import { CommToolSelectStep } from "./steps/CommToolSelectStep";
import { SourceUploadStep } from "./steps/SourceUploadStep";
import { ProvisioningStep } from "./steps/ProvisioningStep";
import { SetupCompleteStep } from "./steps/SetupCompleteStep";
import { useCompleteOnboarding } from "@/hooks/useOnboarding";
import { useOnboardingContext } from "@/contexts/OnboardingContext";
import { useAuthContext } from "@/components/AuthProvider";
import { trackWizardEvent } from "@/lib/telemetry";

// 7-step wizard. Step 5 provisions; step 6 is the explicit "Ready" celebration
// (fixes the old UX where the wizard jumped straight into the product tour).
const STEP_LABELS = [
  "Welcome",
  "Workspace",
  "Platforms",
  "Tools",
  "Source",
  "Setup",
  "Ready",
];

interface Props {
  onDismiss: () => void;
}

export function OnboardingWizard({ onDismiss }: Props) {
  const { user } = useAuthContext();
  const { status, refetch, startTour } = useOnboardingContext();
  const complete = useCompleteOnboarding();

  // Resume from the last saved step, but never auto-start on Provisioning (5)
  // or SetupComplete (6) — those require server-side complete_wizard to have run.
  const [step, setStep] = useState(() => {
    const saved = status?.wizard_current_step ?? 0;
    return saved >= 5 ? 0 : saved;
  });

  useEffect(() => {
    trackWizardEvent("wizard_step_viewed", step);
  }, [step]);

  async function finalize() {
    await complete.mutateAsync();
    setStep(5);
  }

  return (
    <div className="fixed inset-0 bg-background z-50 flex flex-col overflow-y-auto">
      {/* Ambient brand glow */}
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
        workspaceName={status?.workspace_name}
      />

      <div key={step} className="animate-hero-enter relative flex-1 flex items-center justify-center py-10">
        {step === 0 && (
          <WelcomeStep
            username={user?.username ?? "there"}
            onNext={() => setStep(1)}
          />
        )}
        {step === 1 && (
          <WorkspaceStep
            initial={status?.workspace_name}
            suggested={user?.username}
            onBack={() => setStep(0)}
            onNext={() => setStep(2)}
          />
        )}
        {step === 2 && (
          <PlatformSelectStep
            initial={status?.selected_platforms ?? []}
            onBack={() => setStep(1)}
            onNext={() => setStep(3)}
          />
        )}
        {step === 3 && (
          <CommToolSelectStep
            initial={status?.selected_comm_tools ?? []}
            onBack={() => setStep(2)}
            onNext={() => setStep(4)}
          />
        )}
        {step === 4 && (
          <SourceUploadStep
            initialUrl={status?.source_url}
            initialFilename={status?.source_filename}
            initialType={status?.source_type ?? null}
            selectedPlatformIds={status?.selected_platforms ?? []}
            onBack={() => setStep(3)}
            onNext={finalize}
            onSkip={finalize}
          />
        )}
        {step === 5 && (
          <ProvisioningStep
            initialStatus={
              (complete.data?.setup_status ?? status?.setup_status ?? "pending") as
                | "pending"
                | "processing"
                | "completed"
                | "failed"
                | "skipped"
            }
            workspaceName={status?.workspace_name}
            selectedPlatformIds={status?.selected_platforms ?? []}
            selectedToolIds={status?.selected_comm_tools ?? []}
            onDone={async () => {
              await refetch();
              setStep(6);
            }}
          />
        )}
        {step === 6 && (
          <SetupCompleteStep
            workspaceName={status?.workspace_name}
            selectedPlatformIds={status?.selected_platforms ?? []}
            selectedToolIds={status?.selected_comm_tools ?? []}
            onStartTour={() => {
              trackWizardEvent("tour_started", 6);
              onDismiss();
              startTour();
            }}
            onSkipTour={() => {
              trackWizardEvent("tour_skipped", 6);
              onDismiss();
            }}
          />
        )}
      </div>
    </div>
  );
}
