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
import {
  useCompleteCompanyOnboarding,
  useCompanyStatus,
  useSaveCompanyWorkspace,
  useSaveCompanyPlatforms,
  useSaveCompanyCommTools,
  useSaveCompanySource,
} from "@/hooks/useSystemConfig";
import { useOnboardingContext } from "@/contexts/OnboardingContext";
import { useAuthContext } from "@/components/AuthProvider";
import { trackWizardEvent } from "@/lib/telemetry";

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

export function AdminOnboardingWizard({ onDismiss }: Props) {
  const { user } = useAuthContext();
  const { startTour } = useOnboardingContext();
  const { data: companyStatus, refetch } = useCompanyStatus();
  const complete = useCompleteCompanyOnboarding();

  // Company mutations injected into step components
  const saveWorkspace = useSaveCompanyWorkspace();
  const savePlatforms = useSaveCompanyPlatforms();
  const saveCommTools = useSaveCompanyCommTools();
  const saveSource = useSaveCompanySource();

  const [step, setStep] = useState(() => {
    const saved = companyStatus?.wizard_current_step ?? 0;
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
      <WizardProgress
        steps={STEP_LABELS}
        currentStep={step}
        workspaceName={companyStatus?.workspace_name}
      />

      <div className="flex-1 flex items-center justify-center py-10">
        {step === 0 && (
          <WelcomeStep
            username={user?.username ?? "there"}
            onNext={() => setStep(1)}
          />
        )}
        {step === 1 && (
          <WorkspaceStep
            initial={companyStatus?.workspace_name}
            suggested={user?.username}
            onBack={() => setStep(0)}
            onNext={() => setStep(2)}
            overrideSave={saveWorkspace}
          />
        )}
        {step === 2 && (
          <PlatformSelectStep
            initial={companyStatus?.selected_platforms ?? []}
            onBack={() => setStep(1)}
            onNext={() => setStep(3)}
            overrideSave={savePlatforms}
          />
        )}
        {step === 3 && (
          <CommToolSelectStep
            initial={companyStatus?.selected_comm_tools ?? []}
            onBack={() => setStep(2)}
            onNext={() => setStep(4)}
            overrideSave={saveCommTools}
          />
        )}
        {step === 4 && (
          <SourceUploadStep
            initialUrl={companyStatus?.source_url}
            initialFilename={companyStatus?.source_filename}
            initialType={companyStatus?.source_type ?? null}
            selectedPlatformIds={companyStatus?.selected_platforms ?? []}
            onBack={() => setStep(3)}
            onNext={finalize}
            onSkip={finalize}
            overrideSave={saveSource}
          />
        )}
        {step === 5 && (
          <ProvisioningStep
            initialStatus={
              (complete.data?.setup_status ?? companyStatus?.setup_status ?? "pending") as
                | "pending"
                | "processing"
                | "completed"
                | "failed"
                | "skipped"
            }
            workspaceName={companyStatus?.workspace_name}
            selectedPlatformIds={companyStatus?.selected_platforms ?? []}
            selectedToolIds={companyStatus?.selected_comm_tools ?? []}
            onDone={async () => {
              await refetch();
              setStep(6);
            }}
          />
        )}
        {step === 6 && (
          <SetupCompleteStep
            workspaceName={companyStatus?.workspace_name}
            selectedPlatformIds={companyStatus?.selected_platforms ?? []}
            selectedToolIds={companyStatus?.selected_comm_tools ?? []}
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
