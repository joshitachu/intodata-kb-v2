import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { UserWelcomeWizard } from "@/components/onboarding/UserWelcomeWizard";
import type { InviteContext, Persona } from "@/types/onboarding";

/**
 * The member walkthrough replaced a single card reading "your workspace is
 * ready". These tests pin what that card could not do: name the approver, state
 * the persona's own authority and its limits, and hand off to the tour.
 */

const completeOnboarding = vi.fn();
const startTour = vi.fn();

let persona: Persona = "developer";
let invite: InviteContext | null = null;

vi.mock("@/hooks/useOnboarding", () => ({
  useCompleteOnboarding: () => ({
    mutateAsync: completeOnboarding,
    isPending: false,
  }),
}));

vi.mock("@/hooks/useSystemConfig", () => ({
  useCompanyStatus: () => ({ data: { workspace_name: "Northwind" } }),
}));

vi.mock("@/contexts/OnboardingContext", () => ({
  useOnboardingContext: () => ({
    status: { persona, invite_context: invite },
    startTour,
  }),
}));

vi.mock("@/components/AuthProvider", () => ({
  useAuthContext: () => ({ user: { username: "jdoe" } }),
}));

vi.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ hasPermission: () => false }),
}));

vi.mock("@/lib/telemetry", () => ({ trackWizardEvent: vi.fn() }));

/** Walk the wizard to its final screen. */
function advanceToReady() {
  fireEvent.click(screen.getByRole("button", { name: /Show me around/ }));
  fireEvent.click(screen.getByRole("button", { name: /^Next/ }));
  fireEvent.click(screen.getByRole("button", { name: /^Continue/ }));
}

describe("UserWelcomeWizard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    completeOnboarding.mockResolvedValue(undefined);
    persona = "developer";
    invite = null;
  });

  it("opens on a personalized welcome with the workspace name", () => {
    render(<UserWelcomeWizard onDismiss={vi.fn()} />);

    expect(
      screen.getByRole("heading", { name: "Welcome to Northwind, jdoe" }),
    ).toBeInTheDocument();
    // Four screens, not one card.
    expect(screen.getByText("Step 1 of 4")).toBeInTheDocument();
  });

  it("names the approver and shows their note", () => {
    invite = {
      approved_by: "sarah",
      assigned_role: "developer",
      assigned_project_ids: ["werkorder"],
      welcome_note: "Start with the Werkorder docs.",
    };
    render(<UserWelcomeWizard onDismiss={vi.fn()} />);

    expect(screen.getByText("sarah")).toBeInTheDocument();
    expect(screen.getByText("werkorder")).toBeInTheDocument();
    expect(
      screen.getByText("Start with the Werkorder docs."),
    ).toBeInTheDocument();
  });

  it("omits the approver block for an account created without an invite", () => {
    render(<UserWelcomeWizard onDismiss={vi.fn()} />);
    expect(screen.queryByText(/approved your access/)).not.toBeInTheDocument();
  });

  it("states a member's capabilities and where their access stops", () => {
    render(<UserWelcomeWizard onDismiss={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /Show me around/ }));

    expect(
      screen.getByRole("heading", { name: /What you can do as a member/ }),
    ).toBeInTheDocument();
    expect(screen.getByText("Where your access stops")).toBeInTheDocument();
    expect(
      screen.getByText(/You see the projects you are assigned to/),
    ).toBeInTheDocument();
  });

  it("describes admin authority differently from member authority", () => {
    persona = "admin";
    render(<UserWelcomeWizard onDismiss={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /Show me around/ }));

    expect(
      screen.getByRole("heading", { name: /What you can do as an admin/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Approve access and set roles/),
    ).toBeInTheDocument();
    // The boundary an admin actually needs to know.
    expect(
      screen.getByText(/cannot modify the Owner account/),
    ).toBeInTheDocument();
  });

  it("explains how answers are grounded before the final screen", () => {
    render(<UserWelcomeWizard onDismiss={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /Show me around/ }));
    fireEvent.click(screen.getByRole("button", { name: /^Next/ }));

    expect(
      screen.getByRole("heading", { name: /Answers are retrieved, then cited/ }),
    ).toBeInTheDocument();
    expect(screen.getByText("Your question is scoped")).toBeInTheDocument();
  });

  it("completes onboarding and starts the tour when the walkthrough is taken", async () => {
    const onDismiss = vi.fn();
    render(<UserWelcomeWizard onDismiss={onDismiss} />);
    advanceToReady();

    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: /Take the .* walkthrough/ }),
      );
    });

    await waitFor(() => {
      expect(completeOnboarding).toHaveBeenCalled();
      expect(onDismiss).toHaveBeenCalled();
      expect(startTour).toHaveBeenCalled();
    });
  });

  it("completes onboarding without the tour when skipped", async () => {
    const onDismiss = vi.fn();
    render(<UserWelcomeWizard onDismiss={onDismiss} />);
    advanceToReady();

    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: /Skip the walkthrough and start/ }),
      );
    });

    await waitFor(() => {
      expect(completeOnboarding).toHaveBeenCalled();
      expect(onDismiss).toHaveBeenCalled();
    });
    expect(startTour).not.toHaveBeenCalled();
  });

  it("still lets the user out when the completion write fails", async () => {
    // A failed status update must not trap someone behind a wizard they have
    // finished — they would have no way to reach the product at all.
    completeOnboarding.mockRejectedValue(new Error("network"));
    const onDismiss = vi.fn();
    render(<UserWelcomeWizard onDismiss={onDismiss} />);
    advanceToReady();

    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: /Skip the walkthrough and start/ }),
      );
    });

    await waitFor(() => expect(onDismiss).toHaveBeenCalled());
  });

  it("allows going back through every screen", () => {
    render(<UserWelcomeWizard onDismiss={vi.fn()} />);
    advanceToReady();
    expect(screen.getByText("Step 4 of 4")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^Back/ }));
    expect(screen.getByText("Step 3 of 4")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Back/ }));
    expect(screen.getByText("Step 2 of 4")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Back/ }));
    expect(screen.getByText("Step 1 of 4")).toBeInTheDocument();
  });
});
