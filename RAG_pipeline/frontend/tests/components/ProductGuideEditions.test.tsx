import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProductGuide } from "@/components/guide/ProductGuide";

/**
 * `/guide` has two editions: "Using the app" and "Running the workspace".
 *
 * It previously rendered every chapter to every reader, so a member looking up
 * how to ask a question scrolled past Organization, Sources, Observability and
 * the Admin runbook -- four chapters about controls they cannot open.
 */

const push = vi.fn();

let permissions: string[] = [];
let isOwner = false;

vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

vi.mock("@/contexts/OnboardingContext", () => ({
  useOnboardingContext: () => ({
    status: { persona: isOwner ? "owner" : "developer" },
    startTour: vi.fn(),
  }),
}));

vi.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({
    hasPermission: (codename: string) => isOwner || permissions.includes(codename),
    isOwner,
    hasAnyAdminPermission: isOwner || permissions.length > 0,
  }),
}));

/** Chapters that belong only to the "running the workspace" edition. */
const RUNNING_HEADINGS = [
  "Sources and the evidence pipeline",
  "Users, access and projects",
  "Observe and diagnose",
  "Admin runbook",
];

/** Chapters every reader gets, in both editions. */
const SHARED_HEADINGS = [
  "Overview",
  "Architecture map",
  "Chat and Deep Dive",
  "Troubleshooting",
  "Glossary",
];

/** The switcher's own button, distinct from the right-rail shortcut. */
function editionButton(label: string) {
  const group = screen.getByRole("group", { name: "Choose guide edition" });
  return within(group).getByRole("button", { name: label });
}

function setLocation(search = "") {
  window.history.replaceState(null, "", `/guide${search}`);
}

describe("ProductGuide editions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    permissions = [];
    isOwner = false;
    setLocation();
  });

  it("gives a member only the member edition", () => {
    render(<ProductGuide />);

    expect(
      screen.getByRole("heading", { level: 1, name: "Using the app" }),
    ).toBeInTheDocument();
    for (const heading of SHARED_HEADINGS) {
      expect(screen.getByRole("heading", { level: 2, name: heading })).toBeInTheDocument();
    }
    for (const heading of RUNNING_HEADINGS) {
      expect(
        screen.queryByRole("heading", { level: 2, name: heading }),
      ).not.toBeInTheDocument();
    }
  });

  it("does not offer a member the edition switch", () => {
    render(<ProductGuide />);
    // A door into chapters about controls they cannot open is a dead end.
    expect(
      screen.queryByRole("group", { name: "Choose guide edition" }),
    ).not.toBeInTheDocument();
  });

  it("opens an owner on the running edition with every chapter", () => {
    isOwner = true;
    render(<ProductGuide />);

    expect(
      screen.getByRole("heading", { level: 1, name: "Running the workspace" }),
    ).toBeInTheDocument();
    for (const heading of [...SHARED_HEADINGS, ...RUNNING_HEADINGS]) {
      expect(screen.getByRole("heading", { level: 2, name: heading })).toBeInTheDocument();
    }
  });

  it("lets an admin switch to what their members see", () => {
    isOwner = true;
    render(<ProductGuide />);

    expect(
      screen.getByRole("heading", { level: 2, name: "Admin runbook" }),
    ).toBeInTheDocument();

    fireEvent.click(editionButton("Using the app"));

    expect(
      screen.getByRole("heading", { level: 1, name: "Using the app" }),
    ).toBeInTheDocument();
    for (const heading of RUNNING_HEADINGS) {
      expect(
        screen.queryByRole("heading", { level: 2, name: heading }),
      ).not.toBeInTheDocument();
    }
    // Shared chapters must survive the switch.
    expect(
      screen.getByRole("heading", { level: 2, name: "Chat and Deep Dive" }),
    ).toBeInTheDocument();
  });

  it("records the chosen edition in the URL so it can be shared", () => {
    isOwner = true;
    render(<ProductGuide />);

    fireEvent.click(editionButton("Using the app"));
    expect(window.location.search).toContain("edition=using");
  });

  it("honours an explicit ?edition= for a reader who may use it", () => {
    isOwner = true;
    setLocation("?edition=using");
    render(<ProductGuide />);

    expect(
      screen.getByRole("heading", { level: 1, name: "Using the app" }),
    ).toBeInTheDocument();
  });

  it("ignores ?edition=running for a member", () => {
    // Otherwise a shared admin link would expose the admin edition to anyone.
    setLocation("?edition=running");
    render(<ProductGuide />);

    expect(
      screen.getByRole("heading", { level: 1, name: "Using the app" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { level: 2, name: "Admin runbook" }),
    ).not.toBeInTheDocument();
  });

  it("shows a partial admin only the chapters their permissions cover", () => {
    // Edition alone is not enough: an admin who cannot open Organization
    // should not be taught the Organization chapter.
    permissions = ["manage_projects"];
    render(<ProductGuide />);

    expect(
      screen.getByRole("heading", { level: 2, name: "Sources and the evidence pipeline" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { level: 2, name: "Users, access and projects" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { level: 2, name: "Observe and diagnose" }),
    ).not.toBeInTheDocument();
  });

  it("reports the chapter count for the edition on screen", () => {
    isOwner = true;
    render(<ProductGuide />);
    const ownerCount = Number(
      screen.getByText(/^\d+ chapters$/).textContent?.split(" ")[0],
    );

    fireEvent.click(editionButton("Using the app"));
    const memberCount = Number(
      screen.getByText(/^\d+ chapters$/).textContent?.split(" ")[0],
    );

    expect(ownerCount).toBeGreaterThan(memberCount);
    expect(ownerCount - memberCount).toBe(RUNNING_HEADINGS.length);
  });

  it("keeps search inside the current edition", () => {
    // Returning a hidden chapter would scroll to an element that is not
    // rendered, which reads as a broken search box.
    render(<ProductGuide />);

    fireEvent.change(
      screen.getByRole("searchbox", { name: "Search product documentation" }),
      { target: { value: "runbook" } },
    );

    expect(screen.getByText("No matching section")).toBeInTheDocument();
  });

  it("finds an admin chapter by search in the running edition", () => {
    isOwner = true;
    render(<ProductGuide />);

    fireEvent.change(
      screen.getByRole("searchbox", { name: "Search product documentation" }),
      { target: { value: "runbook" } },
    );

    expect(
      screen.getByRole("button", { name: "Open Admin runbook" }),
    ).toBeInTheDocument();
  });
});
