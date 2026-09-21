import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";

/**
 * Deep Dive is a V2 capability, and the toggle has to say so.
 *
 * The V1 runtime accepts `mode` and never reads it
 * (`backend/src/api/routes/conversations.py`), so a Deep Dive turn on a V1
 * thread came back as an ordinary answer with no error and nothing to show for
 * the toggle -- twice mistaken for a broken feature. These assertions are what
 * stops the control offering a choice the lane cannot honour, and they cover
 * the other half too: on V2 the mode is per turn and must stay freely
 * switchable, including in the middle of a thread.
 */

const chatContext = {
  isDeepDiveAvailable: false,
  isLaneLocked: false,
};
vi.mock("@/contexts/ChatContext", () => ({
  useChatContext: () => chatContext,
}));

import { ComposerModeToggle, ModeToggle } from "@/components/ModeToggle";

function deepDiveOption(): HTMLElement {
  return screen.getByRole("radio", { name: /deep dive/i });
}

function chatOption(): HTMLElement {
  return screen.getByRole("radio", { name: /^chat$/i });
}

describe("the mode toggle on a V1 thread", () => {
  beforeEach(() => {
    chatContext.isDeepDiveAvailable = false;
    chatContext.isLaneLocked = true;
  });

  it("refuses Deep Dive and names the lane and the remedy", () => {
    render(<ComposerModeToggle mode="chat" onModeChange={vi.fn()} />);

    const option = deepDiveOption();
    expect(option).toHaveAttribute("aria-disabled", "true");
    expect(option).toHaveAttribute("aria-checked", "false");
    // The option is still called what it is. The reason is its description --
    // a name that grew a sentence would stop matching the control it names.
    expect(option).toHaveAccessibleName("Deep Dive");

    const reason = screen.getByTestId("mode-toggle-lock-reason");
    expect(option).toHaveAttribute("aria-describedby", reason.id);
    // It names the lane that cannot do it, the lane that can, and what to do.
    expect(reason).toHaveTextContent(/deep dive needs v2/i);
    expect(reason).toHaveTextContent(/v1/i);
    expect(reason).toHaveTextContent(/start a new v2 chat/i);
    expect(option).toHaveAttribute("title", expect.stringMatching(/needs v2/i));
  });

  it("answers a click with the reason instead of the mode", () => {
    const onModeChange = vi.fn();
    render(<ComposerModeToggle mode="chat" onModeChange={onModeChange} />);

    const reason = screen.getByTestId("mode-toggle-lock-reason");
    // Available to a screen reader from the start, out of the row until asked
    // for: a permanent sentence in the composer would nag every V1 user, which
    // is most of them.
    expect(reason).toHaveClass("sr-only");
    expect(reason).not.toHaveAttribute("data-revealed");

    fireEvent.click(deepDiveOption());

    // Not `disabled`: a dead button explains nothing and a touch device never
    // sees the tooltip, so the click is received and answered.
    expect(onModeChange).not.toHaveBeenCalled();
    expect(reason).toHaveAttribute("data-revealed", "true");
    expect(reason).not.toHaveClass("sr-only");
  });

  it("points at the lane switch instead when no thread has been started yet", () => {
    chatContext.isLaneLocked = false;
    render(<ComposerModeToggle mode="chat" onModeChange={vi.fn()} />);

    const reason = screen.getByTestId("mode-toggle-lock-reason");
    // The lane is still the user's to pick, so the remedy is not "start a new
    // chat" -- that would send them to replace a thread that does not exist.
    expect(reason).toHaveTextContent(/switch the answering engine above to v2/i);
    expect(reason).not.toHaveTextContent(/start a new v2 chat/i);
  });

  it("never shows Deep Dive as selected, whatever mode it is handed", () => {
    // A lane can be picked after the mode was: the option is refused, so it
    // cannot also be lit up while every turn actually runs as chat.
    render(<ComposerModeToggle mode="deep-dive" onModeChange={vi.fn()} />);

    expect(deepDiveOption()).toHaveAttribute("aria-checked", "false");
    expect(chatOption()).toHaveAttribute("aria-checked", "true");
  });

  it("leaves Chat fully usable", () => {
    const onModeChange = vi.fn();
    render(<ComposerModeToggle mode="chat" onModeChange={onModeChange} />);

    expect(chatOption()).not.toHaveAttribute("aria-disabled");
    fireEvent.click(chatOption());
    expect(onModeChange).toHaveBeenCalledWith("chat");
  });
});

describe("the mode toggle on a V2 thread", () => {
  beforeEach(() => {
    chatContext.isDeepDiveAvailable = true;
    chatContext.isLaneLocked = true;
  });

  it("offers Deep Dive with no lock and no reason attached", () => {
    render(<ComposerModeToggle mode="chat" onModeChange={vi.fn()} />);

    const option = deepDiveOption();
    expect(option).not.toHaveAttribute("aria-disabled");
    expect(option).not.toHaveAttribute("aria-describedby");
    expect(option).not.toHaveAttribute("title");
    expect(screen.queryByTestId("mode-toggle-lock-reason")).toBeNull();
  });

  it("switches mid-thread, in both directions", () => {
    const onModeChange = vi.fn();
    const { rerender } = render(
      <ComposerModeToggle mode="chat" onModeChange={onModeChange} />,
    );

    fireEvent.click(deepDiveOption());
    expect(onModeChange).toHaveBeenLastCalledWith("deep-dive");

    // The lane is fixed for the life of the thread; the mode is not. An open
    // V2 thread must be able to go back to chat for the next turn.
    rerender(<ComposerModeToggle mode="deep-dive" onModeChange={onModeChange} />);
    expect(deepDiveOption()).toHaveAttribute("aria-checked", "true");

    fireEvent.click(chatOption());
    expect(onModeChange).toHaveBeenLastCalledWith("chat");
    expect(onModeChange).toHaveBeenCalledTimes(2);
  });
});

describe("the presentational half of the toggle", () => {
  // `ModeToggle` takes the lock as a prop so exactly one place -- the provider,
  // through `ComposerModeToggle` -- decides whether the lane honours the mode.
  it("refuses the option whenever it is given a reason to", () => {
    const onModeChange = vi.fn();
    render(
      <ModeToggle
        mode="chat"
        onModeChange={onModeChange}
        deepDiveLockReason="Deep Dive needs V2. Reason under test."
      />,
    );

    fireEvent.click(deepDiveOption());
    expect(onModeChange).not.toHaveBeenCalled();
    const reason = screen.getByTestId("mode-toggle-lock-reason");
    expect(reason).toHaveTextContent("Reason under test.");
    expect(reason).toHaveAttribute("data-revealed", "true");
  });

  it("offers it when given none", () => {
    const onModeChange = vi.fn();
    render(<ModeToggle mode="chat" onModeChange={onModeChange} />);

    fireEvent.click(deepDiveOption());
    expect(onModeChange).toHaveBeenCalledWith("deep-dive");
  });
});
