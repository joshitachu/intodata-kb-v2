import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const push = vi.fn();
const refreshUser = vi.fn();
const startTour = vi.fn();
const completeOnboarding = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));

vi.mock("@/components/AuthProvider", () => ({
  useAuthContext: () => ({
    user: { mfa_enabled: false, has_password: false, sso_provider: null },
    refreshUser,
  }),
}));

vi.mock("@/contexts/OnboardingContext", () => ({
  useOnboardingContext: () => ({ startTour }),
}));

vi.mock("@/hooks/useOnboarding", () => ({
  useCompleteOnboarding: () => ({
    mutateAsync: completeOnboarding,
    isPending: false,
  }),
}));

vi.mock("@/hooks/useViewport", () => ({
  useViewport: () => ({ isMobile: false }),
}));

import SecuritySettingsPage from "@/app/settings/security/page";

describe("passwordless security setup", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    push.mockReset();
    refreshUser.mockReset();
    startTour.mockReset();
    completeOnboarding.mockReset();
  });

  it("routes to first-password setup before completing onboarding", async () => {
    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ secret: "SECRET", otpauth_uri: "otpauth://totp/test" }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ message: "MFA enabled", backup_codes: ["BACKUP-CODE"] }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
    refreshUser.mockResolvedValue({
      mfa_enabled: true,
      has_password: false,
      sso_provider: null,
    });

    render(<SecuritySettingsPage />);
    fireEvent.click(screen.getByRole("button", { name: "Set up 2FA" }));
    await screen.findByPlaceholderText("6-digit code");
    fireEvent.change(screen.getByPlaceholderText("6-digit code"), {
      target: { value: "123456" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Verify & enable" }));
    await screen.findByText("MFA is now enabled.");
    fireEvent.click(
      screen.getByRole("button", { name: "I saved these codes, start walkthrough" }),
    );

    await waitFor(() => expect(push).toHaveBeenCalledWith("/settings/profile"));
    expect(completeOnboarding).not.toHaveBeenCalled();
    expect(startTour).not.toHaveBeenCalled();
  });
});
