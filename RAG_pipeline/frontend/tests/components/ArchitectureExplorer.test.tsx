import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ArchitectureExplorer } from "@/components/guide/ArchitectureExplorer";

/**
 * The zoom explorer answers "where does this sit relative to everything else",
 * which the written guide cannot. Its contract is that every level is reachable
 * and every level is escapable.
 */
describe("ArchitectureExplorer", () => {
  it("opens zoomed out on the four platform blocks", () => {
    render(<ArchitectureExplorer />);

    expect(screen.getByText("Sources")).toBeInTheDocument();
    expect(screen.getByText("Intelligence")).toBeInTheDocument();
    expect(screen.getByText("Knowledge base")).toBeInTheDocument();
    expect(screen.getByText("Surfaces")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Zoom out/ })).toBeDisabled();
  });

  it("zooms from a block to its subsystems to the mechanism", () => {
    render(<ArchitectureExplorer />);

    fireEvent.click(screen.getByText("Knowledge base"));
    expect(screen.getByText("Ingestion")).toBeInTheDocument();
    expect(screen.getByText("Revisions")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Ingestion"));
    expect(
      screen.getByRole("heading", { level: 4, name: "Structure-aware chunking" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 4, name: "Two retrieval signals" }),
    ).toBeInTheDocument();
  });

  it("keeps a breadcrumb back to every level already visited", () => {
    render(<ArchitectureExplorer />);
    fireEvent.click(screen.getByText("Surfaces"));
    fireEvent.click(screen.getByText("Access control"));

    fireEvent.click(screen.getByRole("button", { name: "Surfaces" }));
    expect(screen.getByText("Chat and Deep Dive")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Whole platform" }));
    expect(screen.getByText("Intelligence")).toBeInTheDocument();
  });

  it("zooms out one level at a time rather than exiting", () => {
    render(<ArchitectureExplorer />);
    fireEvent.click(screen.getByText("Sources"));
    fireEvent.click(screen.getByText("Code and archives"));

    fireEvent.click(screen.getByRole("button", { name: /Zoom out/ }));
    // Back to the subsystem list, not all the way to the top — losing the
    // reader's place on one click is what makes drilldowns unusable.
    expect(screen.getByText("Runtime signals")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Zoom out/ }));
    expect(screen.getByText("Knowledge base")).toBeInTheDocument();
  });

  it("zooms out on Escape", () => {
    render(<ArchitectureExplorer />);
    fireEvent.click(screen.getByText("Intelligence"));
    expect(screen.getByText("Scan and extract")).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.getByText("Surfaces")).toBeInTheDocument();
  });

  it("links the mechanism to the page where it is visible", () => {
    render(<ArchitectureExplorer />);
    fireEvent.click(screen.getByText("Intelligence"));
    fireEvent.click(screen.getByText("Topology model"));

    expect(screen.getByRole("link", { name: /Open Topology/ })).toHaveAttribute(
      "href",
      "/topology",
    );
  });
});
