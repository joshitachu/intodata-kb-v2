import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const push = vi.fn();
const refreshUser = vi.fn();
let authUser: Record<string, unknown>;

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));

vi.mock("@/components/AuthProvider", () => ({
  useAuthContext: () => ({ user: authUser, refreshUser }),
}));

vi.mock("@/hooks/useViewport", () => ({
  useViewport: () => ({ isMobile: false }),
}));

import ProfilePage from "@/app/settings/profile/page";

function user(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    username: "person",
    email: "person@example.com",
    roles: ["developer"],
    last_login_at: null,
    mfa_enabled: true,
    has_password: false,
    sso_provider: null,
    ...overrides,
  };
}

describe("profile password flow", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    push.mockReset();
    refreshUser.mockReset();
    authUser = user();
  });

  it("creates a first password without asking for a current password", async () => {
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ message: "Password created." }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    refreshUser.mockResolvedValue(user({ has_password: true }));

    render(<ProfilePage />);

    expect(screen.queryByLabelText("Current password")).not.toBeInTheDocument();
    expect(screen.getByText(/Create a password before continuing/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/New password/), {
      target: { value: "first-secure-password" },
    });
    fireEvent.change(screen.getByLabelText("Confirm new password"), {
      target: { value: "first-secure-password" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create password & continue" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/auth/set-password",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ new_password: "first-secure-password" }),
      }),
    ));
    await waitFor(() => expect(refreshUser).toHaveBeenCalled());
    expect(push).toHaveBeenCalledWith("/");
  });

  it("keeps current-password verification for password changes", async () => {
    authUser = user({ has_password: true });
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ message: "Password updated." }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    render(<ProfilePage />);
    fireEvent.change(screen.getByLabelText("Current password"), {
      target: { value: "current-password" },
    });
    fireEvent.change(screen.getByLabelText(/New password/), {
      target: { value: "new-secure-password" },
    });
    fireEvent.change(screen.getByLabelText("Confirm new password"), {
      target: { value: "new-secure-password" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Update password" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/auth/change-password",
      expect.objectContaining({
        body: JSON.stringify({
          current_password: "current-password",
          new_password: "new-secure-password",
        }),
      }),
    ));
  });
});
