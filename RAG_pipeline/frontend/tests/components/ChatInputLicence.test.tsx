import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { ChatInput } from "@/components/ChatInput";
import { LicenceContext } from "@/hooks/useLicence";
import type { LicenceStatus } from "@/lib/licence";

const VALID: LicenceStatus = {
  state: "valid",
  reason: null,
  plan: "basic",
  features: ["chat", "generation"],
  limits: { generations_per_period: 3 },
  period_start: "2026-09-01T00:00:00Z",
  period_end: "2026-10-01T00:00:00Z",
  days_remaining: 20,
  near_expiry: false,
  enforcement: "enforce",
};

function withLicence(status: LicenceStatus | null, children: ReactNode) {
  return (
    <LicenceContext.Provider value={{ status, refresh: () => {} }}>{children}</LicenceContext.Provider>
  );
}

function type(text: string) {
  fireEvent.change(screen.getByRole("textbox"), { target: { value: text } });
}

describe("ChatInput under a licence", () => {
  it("disables sending and states the reason when the licence has expired", () => {
    const onSend = vi.fn();
    render(withLicence({ ...VALID, state: "expired" }, <ChatInput onSend={onSend} isLoading={false} />));

    expect(screen.getByTestId("chat-licence-reason")).toHaveTextContent(/expired on 2026-10-01.*view-only/);
    expect(screen.getByRole("textbox")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();

    fireEvent.submit(screen.getByRole("textbox").closest("form")!);
    expect(onSend).not.toHaveBeenCalled();
  });

  it("disables sending when the plan does not include chat", () => {
    render(withLicence({ ...VALID, features: ["generation"] }, <ChatInput onSend={vi.fn()} isLoading={false} />));
    expect(screen.getByTestId("chat-licence-reason")).toHaveTextContent('The "basic" plan does not include chat.');
    expect(screen.getByRole("textbox")).toBeDisabled();
  });

  it("sends normally with a valid licence", () => {
    const onSend = vi.fn();
    render(withLicence(VALID, <ChatInput onSend={onSend} isLoading={false} />));
    expect(screen.queryByTestId("chat-licence-reason")).not.toBeInTheDocument();
    type("hello");
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    expect(onSend).toHaveBeenCalledWith("hello", undefined);
  });

  it("does not block when enforcement is off or the status is unknown", () => {
    const onSend = vi.fn();
    const { unmount } = render(
      withLicence({ ...VALID, state: "expired", enforcement: "off" }, <ChatInput onSend={onSend} isLoading={false} />),
    );
    expect(screen.getByRole("textbox")).not.toBeDisabled();
    unmount();
    render(withLicence(null, <ChatInput onSend={onSend} isLoading={false} />));
    expect(screen.getByRole("textbox")).not.toBeDisabled();
  });
});
