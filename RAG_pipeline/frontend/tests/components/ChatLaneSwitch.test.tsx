import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

/**
 * The lane control: what it offers, and what it refuses to offer.
 *
 * `fetchJson` is mocked and asserted *unused* on purpose. The composer used to
 * mount a health line here that polled `/api/labs/health` every 30s and printed
 * "Lane ready (<model>)"; it is gone, and a poll reappearing under the composer
 * is exactly the regression this mock catches.
 */
const fetchJsonMock = vi.fn();
vi.mock("@/lib/authedFetch", () => ({
  fetchJson: (url: string, init?: RequestInit) => fetchJsonMock(url, init),
}));

const chatContext = {
  activeLane: "v1" as "v1" | "v2",
  selectedLane: "v1" as "v1" | "v2",
  isLaneLocked: false,
  setSelectedLane: vi.fn(),
};
vi.mock("@/contexts/ChatContext", () => ({
  useChatContext: () => chatContext,
}));

import { ComposerLaneControls, LaneSwitch } from "@/components/chat/LaneSwitch";

/** Any wording the removed health line could have produced. */
const HEALTH_TEXT = /lane ready|lane unreachable|lane status unknown|checking v2/i;

function resetChatContext() {
  fetchJsonMock.mockReset();
  chatContext.activeLane = "v1";
  chatContext.selectedLane = "v1";
  chatContext.isLaneLocked = false;
  chatContext.setSelectedLane.mockClear();
}

describe("LaneSwitch", () => {
  beforeEach(resetChatContext);

  it("offers both lanes under a labelled group, with V1 selected by default", () => {
    render(<LaneSwitch lane="v1" isLocked={false} onLaneChange={vi.fn()} />);

    // The group is labelled now rather than being a bare pair of pills: the
    // row has to say what is being chosen before it says between what.
    expect(
      screen.getByRole("radiogroup", { name: /answering engine/i }),
    ).toBeInTheDocument();

    const options = screen.getAllByRole("radio");
    expect(options).toHaveLength(2);
    expect(screen.getByRole("radio", { name: /V1/ })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByRole("radio", { name: /V2/ })).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });

  it("says what each lane means without a tooltip", () => {
    render(<LaneSwitch lane="v1" isLocked={false} onLaneChange={vi.fn()} />);

    expect(screen.getByRole("radio", { name: /V1/ })).toHaveTextContent(
      "Proven, quick answers",
    );
    expect(screen.getByRole("radio", { name: /V2/ })).toHaveTextContent(
      "Deeper, multi-step research",
    );
  });

  it("badges V2 as new, and only V2", () => {
    render(<LaneSwitch lane="v1" isLocked={false} onLaneChange={vi.fn()} />);

    const badges = screen.getAllByTestId("lane-new-badge");
    expect(badges).toHaveLength(1);
    expect(badges[0]).toHaveTextContent("NEW");
    expect(screen.getByRole("radio", { name: /V2/ })).toContainElement(badges[0]);
    expect(screen.getByRole("radio", { name: /V1/ })).not.toContainElement(
      badges[0],
    );
  });

  it("carries the lane on each option, so the DOM names the choice", () => {
    render(<LaneSwitch lane="v2" isLocked={false} onLaneChange={vi.fn()} />);

    expect(screen.getByRole("radio", { name: /V1/ })).toHaveAttribute(
      "data-lane",
      "v1",
    );
    expect(screen.getByRole("radio", { name: /V2/ })).toHaveAttribute(
      "data-lane",
      "v2",
    );
  });

  it("picks the lane for the next new thread", () => {
    const onLaneChange = vi.fn();
    render(<LaneSwitch lane="v1" isLocked={false} onLaneChange={onLaneChange} />);

    fireEvent.click(screen.getByRole("radio", { name: /V2/ }));

    expect(onLaneChange).toHaveBeenCalledWith("v2");
  });

  it("moves between lanes from the keyboard, with one tab stop for the group", () => {
    const onLaneChange = vi.fn();
    render(<LaneSwitch lane="v1" isLocked={false} onLaneChange={onLaneChange} />);

    // Roving tabindex: Tab reaches the selected lane, arrows move from there.
    expect(screen.getByRole("radio", { name: /V1/ })).toHaveAttribute(
      "tabindex",
      "0",
    );
    expect(screen.getByRole("radio", { name: /V2/ })).toHaveAttribute(
      "tabindex",
      "-1",
    );

    fireEvent.keyDown(screen.getByRole("radio", { name: /V1/ }), {
      key: "ArrowRight",
    });

    expect(onLaneChange).toHaveBeenCalledWith("v2");
  });

  it("shows an open thread's own lane and offers nothing to click", () => {
    const onLaneChange = vi.fn();
    render(<LaneSwitch lane="v2" isLocked onLaneChange={onLaneChange} />);

    // Inert, not disabled: a greyed-out button invites a click whose only
    // possible outcome is nothing happening.
    expect(screen.queryByRole("radiogroup")).toBeNull();
    expect(screen.queryAllByRole("radio")).toHaveLength(0);
    expect(screen.queryAllByRole("button")).toHaveLength(0);

    const locked = screen.getByTestId("lane-switch-locked");
    expect(locked).toHaveAttribute("data-lane", "v2");
    expect(locked).toHaveTextContent("V2");
    // ...and it says why, rather than failing quietly.
    expect(locked.getAttribute("aria-label")).toMatch(/fixed for this thread/i);
    expect(locked.getAttribute("aria-label")).toMatch(/start a new chat/i);
    expect(onLaneChange).not.toHaveBeenCalled();
  });

  it("stays compact when locked: no heading, no second option, no summaries", () => {
    render(<LaneSwitch lane="v2" isLocked onLaneChange={vi.fn()} />);

    // The two-option row is a choice. There is no choice here, so none of its
    // furniture is reserved.
    expect(screen.queryByText(/answering engine/i)).toBeNull();
    expect(screen.queryByTestId("lane-new-badge")).toBeNull();
    expect(screen.queryByText("Proven, quick answers")).toBeNull();
    expect(screen.queryByText("Deeper, multi-step research")).toBeNull();
    expect(screen.queryByText("V1")).toBeNull();
  });
});

describe("ComposerLaneControls", () => {
  beforeEach(resetChatContext);

  it("renders the choice for a new thread and never probes lane health", async () => {
    render(<ComposerLaneControls />);

    expect(
      screen.getByRole("radiogroup", { name: /answering engine/i }),
    ).toBeInTheDocument();
    expect(screen.queryByText(HEALTH_TEXT)).toBeNull();
    await waitFor(() => expect(fetchJsonMock).not.toHaveBeenCalled());
  });

  it("reports no lane health even while V2 is the active lane", async () => {
    chatContext.activeLane = "v2";
    chatContext.selectedLane = "v2";

    render(<ComposerLaneControls />);

    // The health line polled `/api/labs/health` only in this state, which is
    // why this is the case worth pinning.
    expect(screen.queryByText(HEALTH_TEXT)).toBeNull();
    await waitFor(() => expect(fetchJsonMock).not.toHaveBeenCalled());
  });

  it("locks to the open thread's lane, not the switch's selection", () => {
    chatContext.isLaneLocked = true;
    chatContext.activeLane = "v2";
    chatContext.selectedLane = "v1";

    render(<ComposerLaneControls />);

    expect(screen.getByTestId("lane-switch-locked")).toHaveAttribute(
      "data-lane",
      "v2",
    );
  });
});
