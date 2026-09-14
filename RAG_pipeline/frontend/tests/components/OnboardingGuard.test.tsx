import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { OnboardingGuard } from "@/components/onboarding/OnboardingGuard";
import type { OnboardingStatus, Persona } from "@/types/onboarding";

/**
 * Which wizard a user gets.
 *
 * The guard used to branch on `is_superadmin` alone: an Admin who was not the
 * deployment Owner fell through to the "everyone else" branch, and a member
 * joining an already-configured workspace could be shown a provisioning flow
 * they had no reason to run.
 */

let pathname = "/";
let user: { is_superadmin: boolean; onboarding_completed: boolean } | null = null;
let authLoading = false;
let status: Partial<OnboardingStatus> | null = null;
let statusLoading = false;
let statusError = false;

vi.mock("next/navigation", () => ({ usePathname: () => pathname }));

vi.mock("@/components/AuthProvider", () => ({
  useAuthContext: () => ({ user, isLoading: authLoading }),
}));

vi.mock("@/contexts/OnboardingContext", () => ({
  useOnboardingContext: () => ({
    status,
    isLoading: statusLoading,
    isError: statusError,
  }),
}));

vi.mock("@/components/onboarding/AdminOnboardingWizard", () => ({
  AdminOnboardingWizard: () => <div>PROVISIONING WIZARD</div>,
}));

vi.mock("@/components/onboarding/UserWelcomeWizard", () => ({
  UserWelcomeWizard: () => <div>MEMBER WALKTHROUGH</div>,
}));

function statusFor(
  persona: Persona,
  { companyDone, userDone }: { companyDone: boolean; userDone: boolean },
) {
  return {
    persona,
    company_wizard_completed: companyDone,
    wizard_completed: userDone,
  };
}

function renderGuard() {
  return render(
    <OnboardingGuard>
      <div>APP</div>
    </OnboardingGuard>,
  );
}

describe("OnboardingGuard", () => {
  beforeEach(() => {
    pathname = "/";
    authLoading = false;
    statusLoading = false;
    statusError = false;
    user = { is_superadmin: false, onboarding_completed: false };
    status = null;
  });

  it("shows the provisioning wizard to an Owner of an unconfigured deployment", () => {
    user = { is_superadmin: true, onboarding_completed: false };
    status = statusFor("owner", { companyDone: false, userDone: false });
    renderGuard();

    expect(screen.getByText("PROVISIONING WIZARD")).toBeInTheDocument();
  });

  it("gives a non-Owner admin the member walkthrough, not a bare welcome", () => {
    // The regression: an Admin is not a superadmin, so the old guard treated
    // them as an ordinary member with nothing to be told.
    status = statusFor("admin", { companyDone: true, userDone: false });
    renderGuard();

    expect(screen.getByText("MEMBER WALKTHROUGH")).toBeInTheDocument();
    expect(screen.queryByText("PROVISIONING WIZARD")).not.toBeInTheDocument();
  });

  it("does not hand an admin a provisioning flow for a configured workspace", () => {
    status = statusFor("admin", { companyDone: false, userDone: false });
    renderGuard();

    expect(screen.getByText("MEMBER WALKTHROUGH")).toBeInTheDocument();
    expect(screen.queryByText("PROVISIONING WIZARD")).not.toBeInTheDocument();
  });

  it("shows nothing once both wizards are complete", () => {
    status = statusFor("developer", { companyDone: true, userDone: true });
    renderGuard();

    expect(screen.getByText("APP")).toBeInTheDocument();
    expect(screen.queryByText("MEMBER WALKTHROUGH")).not.toBeInTheDocument();
    expect(screen.queryByText("PROVISIONING WIZARD")).not.toBeInTheDocument();
  });

  it.each([
    "/login",
    "/onboarding",
    "/settings/security",
    "/guide",
  ])("never covers %s", (path) => {
    pathname = path;
    status = statusFor("developer", { companyDone: true, userDone: false });
    renderGuard();

    // Security setup is itself a mandatory first-run step; overlaying the
    // wizard would hide the form the user was just sent to.
    expect(screen.getByText("APP")).toBeInTheDocument();
    expect(screen.queryByText("MEMBER WALKTHROUGH")).not.toBeInTheDocument();
  });

  it("renders the app when onboarding status cannot be fetched", () => {
    statusError = true;
    status = null;
    renderGuard();

    // Blocking here would lock a user out of a working product over an
    // onboarding-flag lookup.
    expect(screen.getByText("APP")).toBeInTheDocument();
  });

  it("renders the app for an anonymous visitor", () => {
    user = null;
    renderGuard();
    expect(screen.getByText("APP")).toBeInTheDocument();
  });

  /**
   * The guard used to `return null` while the onboarding status was in flight,
   * unmounting the entire application for the duration of a flag lookup.
   *
   * On a cold load /auth/me and /onboarding/status race: the instant `user`
   * landed with the status still pending, the whole tree blanked and then
   * remounted — which discards page-local state (app/docs/page.tsx resets
   * `selectedDoc` to null). If the status request failed, `isLoading` stayed
   * true across the retry backoff (1+2+4s) and the app was blank for ~7
   * seconds before the isError branch let it through.
   */
  describe("while the onboarding status is still unknown", () => {
    it("renders the app instead of blanking the screen", () => {
      statusLoading = true;
      status = null;
      renderGuard();

      expect(screen.getByText("APP")).toBeInTheDocument();
      expect(screen.queryByText("MEMBER WALKTHROUGH")).not.toBeInTheDocument();
      expect(screen.queryByText("PROVISIONING WIZARD")).not.toBeInTheDocument();
    });

    it("renders the app for a superadmin too, without guessing a wizard", () => {
      // The reason a blank screen was chosen in the first place: deciding
      // early is worse. `companyDone` defaults to false while unknown, so a
      // superadmin would be latched into the provisioning wizard — and
      // `sessionStarted` is a ref, so that decision would stick for the whole
      // session even on an already-configured deployment. The decision waits;
      // the app does not.
      user = { is_superadmin: true, onboarding_completed: false };
      statusLoading = true;
      status = null;
      renderGuard();

      expect(screen.getByText("APP")).toBeInTheDocument();
      expect(screen.queryByText("PROVISIONING WIZARD")).not.toBeInTheDocument();
    });

    it("shows the right wizard once the flags arrive, over the mounted app", () => {
      user = { is_superadmin: true, onboarding_completed: false };
      statusLoading = true;
      status = null;
      const { rerender } = renderGuard();

      expect(screen.getByText("APP")).toBeInTheDocument();

      // Status lands: the deployment is already provisioned, so this owner
      // gets the member walkthrough — not the setup flow an early latch would
      // have pinned.
      statusLoading = false;
      status = statusFor("owner", { companyDone: true, userDone: false });
      rerender(
        <OnboardingGuard>
          <div>APP</div>
        </OnboardingGuard>,
      );

      expect(screen.getByText("APP")).toBeInTheDocument();
      expect(screen.getByText("MEMBER WALKTHROUGH")).toBeInTheDocument();
      expect(screen.queryByText("PROVISIONING WIZARD")).not.toBeInTheDocument();
    });
  });
});
