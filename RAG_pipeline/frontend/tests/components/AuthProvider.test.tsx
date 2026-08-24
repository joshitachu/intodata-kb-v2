import { render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const replace = vi.fn();
let pathname = "/";
let authState: Record<string, unknown>;

vi.mock("next/navigation", () => ({
  usePathname: () => pathname,
  useRouter: () => ({ replace }),
}));

vi.mock("@/hooks/useAuth", () => ({
  useAuth: () => authState,
}));

import { AuthProvider } from "@/components/AuthProvider";

function user(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    username: "person",
    email: "person@example.com",
    is_active: true,
    is_superadmin: false,
    roles: ["developer"],
    permissions: [],
    onboarding_completed: true,
    tour_completed: true,
    mfa_enabled: true,
    last_login_at: null,
    has_password: true,
    sso_provider: null,
    ...overrides,
  };
}

describe("AuthProvider setup guards", () => {
  beforeEach(() => {
    replace.mockReset();
    pathname = "/";
    authState = {
      user: user(),
      isLoading: false,
      isAuthenticated: true,
      status: "authenticated",
      isDegraded: false,
      logout: vi.fn(),
      loginAs: vi.fn(),
      refreshUser: vi.fn(),
    };
  });

  it("sends a passwordless MFA-authenticated user to first-password setup", async () => {
    authState.user = user({ has_password: false });
    render(<AuthProvider><div>app</div></AuthProvider>);

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/settings/profile"));
  });

  it("requires MFA before first-password setup", async () => {
    authState.user = user({ mfa_enabled: false, has_password: false });
    render(<AuthProvider><div>app</div></AuthProvider>);

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/settings/security"));
    expect(replace).not.toHaveBeenCalledWith("/settings/profile");
  });

  it("allows the required first-password page", async () => {
    pathname = "/settings/profile";
    authState.user = user({ has_password: false });
    render(<AuthProvider><div>setup</div></AuthProvider>);

    await waitFor(() => expect(replace).not.toHaveBeenCalled());
  });

  it("does not force a password on an SSO-only account", async () => {
    authState.user = user({ has_password: false, sso_provider: "microsoft" });
    render(<AuthProvider><div>app</div></AuthProvider>);

    await waitFor(() => expect(replace).not.toHaveBeenCalled());
  });
});

// Only a definitive 401 may redirect. Treating an unreachable backend as a
// logout is what bounced users to /login mid-session.
describe("AuthProvider redirect gating", () => {
  beforeEach(() => {
    replace.mockReset();
    pathname = "/topology";
    authState = {
      user: null,
      isLoading: false,
      isAuthenticated: false,
      status: "unauthenticated",
      isDegraded: false,
      logout: vi.fn(),
      loginAs: vi.fn(),
      refreshUser: vi.fn(),
    };
  });

  it("redirects to login when the session is definitively gone", async () => {
    render(<AuthProvider><div>app</div></AuthProvider>);

    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith("/login?redirect=%2Ftopology"),
    );
  });

  it("does not redirect while the backend is unreachable", async () => {
    authState.status = "unavailable";
    authState.isDegraded = true;
    const { queryByText } = render(<AuthProvider><div>app</div></AuthProvider>);

    await waitFor(() => expect(queryByText(/Reconnecting/)).not.toBeNull());
    expect(replace).not.toHaveBeenCalled();
  });

  it("keeps rendering the app when a live session goes degraded", async () => {
    authState.user = user();
    authState.isAuthenticated = true;
    authState.status = "unavailable";
    authState.isDegraded = true;
    const { queryByText } = render(<AuthProvider><div>app</div></AuthProvider>);

    await waitFor(() => expect(queryByText("app")).not.toBeNull());
    expect(replace).not.toHaveBeenCalled();
  });

  it("does not redirect while auth is still loading", async () => {
    authState.status = "loading";
    authState.isLoading = true;
    render(<AuthProvider><div>app</div></AuthProvider>);

    await waitFor(() => expect(replace).not.toHaveBeenCalled());
  });
});
