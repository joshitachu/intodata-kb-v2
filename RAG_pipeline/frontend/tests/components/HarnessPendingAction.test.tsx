import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HarnessPendingAction } from "@/components/HarnessPendingAction";

describe("HarnessPendingAction", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders clarification labels and submits only the selected value", async () => {
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValueOnce(
      new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }),
    );

    render(
      <HarnessPendingAction action={{
        workflow_run_id: "42",
        interrupt_id: 8,
        expected_version: 2,
        kind: "clarification",
        prompt: "Which project should I use?",
        choices: [{ label: "Customer API", value: "customer-api" }],
      }} />,
    );

    expect(screen.queryByText("customer-api")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Customer API" }));

    await waitFor(() => expect(screen.getByText("Response received")).toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/harness/workflows/42/resume",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ interrupt_id: 8, expected_version: 2, response: "customer-api" }),
      }),
    );
  });

  it("offers approve, decline, and cancel without displaying internal fields", async () => {
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValueOnce(
      new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }),
    );

    render(
      <HarnessPendingAction action={{
        workflow_run_id: "run/7",
        interrupt_id: 3,
        expected_version: 4,
        kind: "approval",
        prompt: "Approve this documentation update?",
        choices: [],
      }} />,
    );

    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Decline" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/harness/workflows/run%2F7/cancel",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ expected_version: 4 }) }),
    ));
  });

  it("shows a safe error when an action fails", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ error: "This request expired" }), {
        status: 409,
        headers: { "Content-Type": "application/json" },
      }),
    );

    render(
      <HarnessPendingAction action={{
        workflow_run_id: "9",
        interrupt_id: 5,
        expected_version: 6,
        kind: "approval",
        prompt: "Approve this update?",
        choices: [],
      }} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Decline" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("This request expired");
  });

  it("accepts free-text clarification and renders returned artifacts", async () => {
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({
        message: "Project documentation generated",
        run: {
          artifact_links: [{ label: "Open documentation", url: "/projects/new-project/docs" }],
        },
      }), { status: 200, headers: { "Content-Type": "application/json" } }),
    );

    render(
      <HarnessPendingAction action={{
        workflow_run_id: "12",
        interrupt_id: 10,
        expected_version: 3,
        kind: "clarification",
        prompt: "Which Git repository should I attach?",
        choices: [],
      }} />,
    );

    fireEvent.change(screen.getByRole("textbox", { name: "Workflow response" }), {
      target: { value: "https://github.com/example/new-project" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Continue workflow" }));

    expect(await screen.findByText("Project documentation generated")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open documentation" })).toHaveAttribute(
      "href",
      "/projects/new-project/docs",
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/harness/workflows/12/resume",
      expect.objectContaining({
        body: JSON.stringify({
          interrupt_id: 10,
          expected_version: 3,
          response: "https://github.com/example/new-project",
        }),
      }),
    );
  });
});
