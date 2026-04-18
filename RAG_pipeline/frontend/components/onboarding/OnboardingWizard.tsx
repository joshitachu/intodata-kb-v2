"use client";

import { useState } from "react";
import { WizardProgress } from "./WizardProgress";
import { WelcomeStep } from "./steps/WelcomeStep";
import { WorkspaceStep } from "./steps/WorkspaceStep";
import { PlatformSelectStep } from "./steps/PlatformSelectStep";
import { CommToolSelectStep } from "./steps/CommToolSelectStep";
import { SourceUploadStep } from "./steps/SourceUploadStep";
import { ProvisioningStep } from "./steps/ProvisioningStep";
import { useCompleteOnboarding } from "@/hooks/useOnboarding";
import { useOnboardingContext } from "@/contexts/OnboardingContext";
import { useAuthContext } from "@/components/AuthProvider";

// Six-step wizard. Progress-bar labels are shorter than the step headings.
const STEP_LABELS = [
  "Welcome",
  "Workspace",
  "Platforms",
  "Tools",
  "Source",
  "Ready",
];

export function OnboardingWizard() {
  const { user } = useAuthContext();
  const { status, refetch, startTour } = useOnboardingContext();
  const complete = useCompleteOnboarding();

  // Resume from the last saved step, but never auto-start at Provisioning (5)
  // since that step requires `complete_wizard` to have run on the backend.
  const [step, setStep] = useState(() => {
    const saved = status?.wizard_current_step ?? 0;
    return saved >= 5 ? 0 : saved;
  });

  async function finalize() {
    await complete.mutateAsync();
    setStep(5);
  }

  return (
    <div className="fixed inset-0 bg-background z-50 flex flex-col overflow-y-auto">
      <WizardProgress steps={STEP_LABELS} currentStep={step} />

      <div className="flex-1 flex items-center justify-center">
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
              startTour();
            }}
          />
        )}
      </div>
    </div>
  );
}
