import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProductTour } from "@/components/tour/ProductTour";
import { isOversize, overlapArea } from "@/components/tour/geometry";
import { CARD_WIDTH, place } from "@/components/tour/placement";
import { resolveTarget } from "@/components/tour/useTargetRect";
import type { Persona } from "@/types/onboarding";

/**
 * Regression cover for the walkthrough the driver.js tour could not provide:
 * role-filtered steps, resumable progress, and skipping a step whose anchor
 * never renders instead of spotlighting an empty page.
 */

const push = vi.fn();
const completeTour = vi.fn();
const saveStep = vi.fn();

let pathname = "/";
let tourActive = true;
let persona: Persona = "developer";
let tourVariant: string | null = null;
let seenSteps: string[] = [];
let permissions: string[] = [];
const stopTour = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
  usePathname: () => pathname,
}));

vi.mock("@/contexts/OnboardingContext", () => ({
  useOnboardingContext: () => ({
    tourActive,
    stopTour,
    status: {
      persona,
      tour_variant: tourVariant,
      tour_seen_steps: seenSteps,
    },
  }),
}));

vi.mock("@/hooks/useOnboarding", () => ({
  useCompleteTour: () => ({ mutate: completeTour }),
  useSaveTourStep: () => ({ mutate: saveStep }),
}));

vi.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({
    hasPermission: (codename: string) => permissions.includes(codename),
  }),
}));

vi.mock("@/lib/telemetry", () => ({ trackWizardEvent: vi.fn() }));

/** Mount an element the tour can anchor to. */
function mountTarget(tourId: string) {
  const node = document.createElement("div");
  node.setAttribute("data-tour", tourId);
  document.body.appendChild(node);
  return node;
}

describe("ProductTour", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    document.body.innerHTML = "";
    pathname = "/";
    tourActive = true;
    persona = "developer";
    tourVariant = null;
    seenSteps = [];
    permissions = ["query_rag"];
  });

  it("renders nothing while the tour is inactive", () => {
    tourActive = false;
    const { container } = render(<ProductTour />);
    expect(container).toBeEmptyDOMElement();
  });

  it("spotlights the first step once its target exists", async () => {
    mountTarget("chat-input");
    render(<ProductTour />);

    expect(
      await screen.findByRole("heading", { name: "Ask in plain language" }),
    ).toBeInTheDocument();
    // Step count reflects the filtered list, not the full definition.
    expect(screen.getByText(/^1\//)).toBeInTheDocument();
  });

  it("gives an admin more steps than a plain member", async () => {
    mountTarget("chat-input");
    permissions = ["query_rag", "manage_users", "manage_projects", "ingest_documents"];
    persona = "admin";
    render(<ProductTour />);

    const adminTotal = (
      await screen.findByText(/^1\/\d+$/)
    ).textContent?.split("/")[1];

    document.body.innerHTML = "";
    mountTarget("chat-input");
    permissions = ["query_rag"];
    persona = "developer";
    const { unmount } = render(<ProductTour />);
    const memberTotal = (
      await screen.findByText(/^1\/\d+$/)
    ).textContent?.split("/")[1];
    unmount();

    expect(Number(adminTotal)).toBeGreaterThan(Number(memberTotal));
  });

  it("drops steps the user lacks permission for", async () => {
    mountTarget("chat-input");
    // `ingest_documents` gates the uploaded-documents step.
    permissions = ["query_rag"];
    render(<ProductTour />);

    await screen.findByRole("heading", { name: "Ask in plain language" });
    const total = Number(
      screen.getByText(/^1\/\d+$/).textContent?.split("/")[1],
    );

    // The member tour is 10 steps with uploads, 8 without: both document
    // steps are gated on `ingest_documents`.
    expect(total).toBe(8);
  });

  it("advances and persists the step id, not just an index", async () => {
    mountTarget("chat-input");
    mountTarget("project-selector");
    render(<ProductTour />);

    const next = await screen.findByRole("button", { name: /Next/ });
    await act(async () => {
      next.click();
    });

    expect(saveStep).toHaveBeenCalledWith(
      expect.objectContaining({ step_id: "chat.input", variant: "developer" }),
    );
  });

  it("resumes at the first unseen step of the same walkthrough", async () => {
    mountTarget("project-selector");
    tourVariant = "developer";
    seenSteps = ["chat.input"];
    render(<ProductTour />);

    expect(
      await screen.findByRole("heading", {
        name: "Answers are scoped to a project",
      }),
    ).toBeInTheDocument();
  });

  it("restarts rather than resuming when the persona changed", async () => {
    mountTarget("chat-input");
    // Progress was recorded against the member tour; this user is now an admin,
    // whose walkthrough is a different list. Resuming by position would skip
    // the steps that were added.
    tourVariant = "developer";
    seenSteps = ["chat.input", "chat.project-scope"];
    persona = "admin";
    permissions = ["query_rag", "manage_users", "manage_projects"];
    render(<ProductTour />);

    expect(
      await screen.findByRole("heading", { name: "Ask in plain language" }),
    ).toBeInTheDocument();
  });

  it("skips a step whose anchor never appears", async () => {
    // The realistic case: the page exists but this build does not render the
    // element the step points at. That step must be dropped and the next one
    // shown, rather than opening the spotlight over nothing.
    mountTarget("project-selector");
    render(<ProductTour />);

    expect(
      await screen.findByRole(
        "heading",
        { name: "Answers are scoped to a project" },
        { timeout: 6000 },
      ),
    ).toBeInTheDocument();
    // The dropped step is gone from the count, not merely hidden.
    expect(screen.getByText(/^1\/7$/)).toBeInTheDocument();
  }, 12000);

  it("skips a whole chapter, not just a step", async () => {
    // The administrative chapters are long by design. Someone who does not run
    // the deployment must be able to leave one without clicking through it.
    mountTarget("chat-input");
    mountTarget("sidebar");
    render(<ProductTour />);

    await screen.findByRole("heading", { name: "Ask in plain language" });

    await act(async () => {
      screen.getByRole("button", { name: "Skip section" }).click();
    });

    // Lands on the first step of the next chapter, not the next step.
    expect(
      await screen.findByRole("heading", {
        name: "Everything else is in the sidebar",
      }),
    ).toBeInTheDocument();
  });

  it("offers no section skip on the last chapter", async () => {
    // The closing step is its own chapter; skipping it would mean skipping to
    // nothing.
    // Resume straight onto the closing step rather than clicking through the
    // whole tour: every unresolved anchor on the way costs a real timeout.
    tourVariant = "developer";
    seenSteps = [
      "chat.input",
      "chat.project-scope",
      "chat.modes",
      "nav.sidebar",
      "evidence.docs",
      "evidence.docs-pdf",
      "evidence.topology",
    ];
    permissions = ["query_rag"];
    render(<ProductTour />);

    await screen.findByRole("heading", { name: "That is the whole loop" });
    expect(
      screen.queryByRole("button", { name: "Skip section" }),
    ).not.toBeInTheDocument();
  });

  it("dims the page before the first anchor has resolved", () => {
    // Nothing is mounted to anchor to. The overlay must still be painted:
    // rendering nothing here is what made the tour open on an undimmed page.
    render(<ProductTour />);

    expect(document.querySelector(".tour-scrim")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("never unmounts the overlay or the card across a step change", async () => {
    // Step 2's anchor does not exist, so this is the worst case: a long
    // handoff. The dim layer must survive it as the same DOM node, and the
    // card must fade rather than vanish.
    mountTarget("chat-input");
    mountTarget("mode-toggle");
    render(<ProductTour />);

    await screen.findByRole("heading", { name: "Ask in plain language" });
    const scrim = document.querySelector(".tour-scrim");
    expect(scrim).toBeInTheDocument();

    await act(async () => {
      screen.getByRole("button", { name: /Next/ }).click();
    });

    // The outgoing step keeps its own copy while the next one resolves, so the
    // card's controls always match what is written on it.
    expect(
      screen.getByRole("heading", { name: "Ask in plain language" }),
    ).toBeInTheDocument();
    expect(document.querySelector(".tour-scrim")).toBe(scrim);

    await waitFor(() =>
      expect(screen.getByRole("dialog")).toHaveStyle({ opacity: "0" }),
    );
    expect(document.querySelector(".tour-scrim")).toBe(scrim);

    await screen.findByRole(
      "heading",
      { name: "Two speeds, on purpose" },
      { timeout: 6000 },
    );
    expect(document.querySelector(".tour-scrim")).toBe(scrim);
    expect(screen.getByRole("dialog")).toHaveStyle({ opacity: "1" });
  }, 12000);

  it("shows a waiting affordance while a slow step resolves", async () => {
    mountTarget("chat-input");
    mountTarget("mode-toggle");
    render(<ProductTour />);

    await screen.findByRole("heading", { name: "Ask in plain language" });
    expect(document.querySelector("[data-tour-progress]")).toHaveAttribute(
      "data-active",
      "false",
    );

    await act(async () => {
      screen.getByRole("button", { name: /Next/ }).click();
    });

    await waitFor(() =>
      expect(document.querySelector("[data-tour-progress]")).toHaveAttribute(
        "data-active",
        "true",
      ),
    );

    // And it stops as soon as the tour is settled again.
    await screen.findByRole(
      "heading",
      { name: "Two speeds, on purpose" },
      { timeout: 6000 },
    );
    expect(document.querySelector("[data-tour-progress]")).toHaveAttribute(
      "data-active",
      "false",
    );
  }, 12000);

  it("still advances when the user asks for reduced motion", async () => {
    // With transitions disabled the fade-out/fade-in choreography would read as
    // the card blinking off and on, so the engine skips it entirely. That path
    // must still commit steps.
    const original = window.matchMedia;
    window.matchMedia = ((query: string) => ({
      matches: query.includes("prefers-reduced-motion"),
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia;

    try {
      mountTarget("chat-input");
      mountTarget("project-selector");
      render(<ProductTour />);

      await screen.findByRole("heading", { name: "Ask in plain language" });
      await act(async () => {
        screen.getByRole("button", { name: /Next/ }).click();
      });

      expect(
        await screen.findByRole("heading", {
          name: "Answers are scoped to a project",
        }),
      ).toBeInTheDocument();
    } finally {
      window.matchMedia = original;
    }
  });

  it("marks the tour complete with its variant when dismissed", async () => {
    mountTarget("chat-input");
    render(<ProductTour />);

    const close = await screen.findByRole("button", { name: "Close walkthrough" });
    await act(async () => {
      close.click();
    });

    await waitFor(() => {
      expect(stopTour).toHaveBeenCalled();
      expect(completeTour).toHaveBeenCalledWith("developer");
    });
  });
});

/**
 * Pure parts of the engine: the "is this worth spotlighting" verdict, card
 * placement, and anchor resolution. These are unit-testable in a way the
 * choreography is not, because jsdom has no layout — every element measures
 * 0x0, so anything geometric has to be exercised with explicit numbers.
 */
describe("walkthrough geometry", () => {
  const viewport = { width: 1440, height: 900 };
  const cardBox = (placement: { top: number; left: number }, height: number) => ({
    top: placement.top,
    left: placement.left,
    width: CARD_WIDTH,
    height,
  });

  it("treats a page-sized anchor as not worth spotlighting", () => {
    // Several steps used to point at page-root containers. Dimming everything
    // except 90% of the screen highlights nothing.
    expect(isOversize({ top: 0, left: 0, width: 1400, height: 820 }, viewport)).toBe(true);
    expect(isOversize({ top: 40, left: 40, width: 320, height: 110 }, viewport)).toBe(false);
  });

  it("does not guess the verdict without a measured viewport", () => {
    expect(
      isOversize({ top: 0, left: 0, width: 1400, height: 820 }, { width: 0, height: 0 }),
    ).toBe(false);
  });

  it("keeps the card off the element it is describing", () => {
    // A short target at the very bottom: "bottom" has no room, and clamping a
    // bottom-placed card back into view drops it on top of the target.
    const target = { top: 840, left: 600, width: 240, height: 48 };
    const placement = place(target, "bottom", 260, viewport);

    expect(overlapArea(cardBox(placement, 260), target)).toBe(0);
  });

  it("never places the card outside the viewport", () => {
    const target = { top: 10, left: 1380, width: 50, height: 40 };
    const placement = place(target, "right", 260, viewport);

    expect(placement.left).toBeGreaterThanOrEqual(0);
    expect(placement.left + CARD_WIDTH).toBeLessThanOrEqual(viewport.width);
    expect(placement.top).toBeGreaterThanOrEqual(0);
    expect(placement.top + 260).toBeLessThanOrEqual(viewport.height);
  });

  it("centres the card when there is nothing to anchor to", () => {
    expect(place(null, "bottom", 260, viewport).side).toBe("center");
  });
});

describe("anchor resolution", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("honours preference order rather than document order", () => {
    // The always-rendered fallback sits earlier in the DOM than the specific
    // control, so a plain querySelector on the list would pick the wrong one.
    mountTarget("docs-header");
    mountTarget("docs-doc-list");

    const resolved = resolveTarget(
      '[data-tour="docs-doc-list"], [data-tour="docs-header"]',
    );

    expect(resolved?.element.getAttribute("data-tour")).toBe("docs-doc-list");
  });

  it("falls back when the preferred anchor is not rendered", () => {
    // The empty-deployment case: no generated docs, so only the page header.
    mountTarget("docs-header");

    const resolved = resolveTarget(
      '[data-tour="docs-doc-list"], [data-tour="docs-header"]',
    );

    expect(resolved?.element.getAttribute("data-tour")).toBe("docs-header");
  });

  it("resolves nothing when no candidate exists", () => {
    expect(resolveTarget('[data-tour="not-here"]')).toBeNull();
  });
});
