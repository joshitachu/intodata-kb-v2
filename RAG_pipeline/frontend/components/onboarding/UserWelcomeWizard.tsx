"use client";

import { useCompleteOnboarding } from "@/hooks/useOnboarding";
import { useCompanyStatus } from "@/hooks/useSystemConfig";
import { useAuthContext } from "@/components/AuthProvider";
import { UserWelcomeStep } from "./steps/UserWelcomeStep";

interface Props {
  onDismiss: () => void;
}

export function UserWelcomeWizard({ onDismiss }: Props) {
  const { user } = useAuthContext();
  const { data: companyStatus } = useCompanyStatus();
  const complete = useCompleteOnboarding();

  async function handleDone() {
    await complete.mutateAsync();
    onDismiss();
  }

  return (
    <div className="fixed inset-0 bg-background z-50 flex flex-col overflow-y-auto">
      <div className="flex-1 flex items-center justify-center py-10">
        <UserWelcomeStep
          username={user?.username ?? "there"}
          companyStatus={companyStatus}
          onNext={handleDone}
        />
      </div>
    </div>
  );
}
