import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React, { type ReactNode } from "react";

// Mock next/navigation before any imports that use it
const mockPush = vi.fn();
const mockRefresh = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, refresh: mockRefresh }),
}));

import { renderHook, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { refreshSession, useAuth } from "@/hooks/useAuth";
import {
  requestSessionRevalidation,
  resetSessionRevalidationThrottle,
} from "@/lib/session";

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return function Wrapper({ children }: { children: ReactNode }) {
    return React.createElement(QueryClientProvider, { client: queryClient }, children);
  };
}

function mockUser(overrides = {}) {
  return {
    id: 1,
    username: "admin",
    email: "admin@test.com",
    is_active: true,
    is_superadmin: true,
    roles: ["admin"],
    permissions: ["manage_users"],
    onboarding_completed: true,
    tour_completed: true,
    mfa_enabled: true,
    ...overrides,
  };
}

describe("useAuth", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("starts in loading state", () => {
    vi.spyOn(global, "fetch").mockImplementationOnce(
      () => new Promise<Response>(() => {})
    );

    const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() });
    expect(result.current.isLoading).toBe(true);
    expect(result.current.user).toBeNull();
  });

  it("sets user on successful auth", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(mockUser()), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );

    const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.isAuthenticated).toBe(true);
    expect(result.current.user?.username).toBe("admin");
  });

  it("sets null user on 401", async () => {
    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(new Response(null, { status: 401 }));

    const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.isAuthenticated).toBe(false);
    expect(result.current.user).toBeNull();
  });

  it("refreshes an expired access token on startup", async () => {
    const fetchMock = vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(new Response(null, { status: 200 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify(mockUser()), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      );

    const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.isAuthenticated).toBe(true);
    });

    expect(result.current.user?.username).toBe("admin");
    expect(result.current.status).toBe("authenticated");
    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/auth/me", { cache: "no-store" });
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/auth/refresh", {
      method: "POST",
      cache: "no-store",
    });
    expect(fetchMock).toHaveBeenNthCalledWith(3, "/api/auth/me", { cache: "no-store" });
  });

  it("logout clears user and redirects to login", async () => {
    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify(mockUser({ roles: [], permissions: [] })), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      )
      .mockResolvedValueOnce(new Response(null, { status: 200 }));

    const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() });

    await waitFor(() => {
      expect(result.current.isAuthenticated).toBe(true);
    });

    await act(async () => {
      await result.current.logout();
    });

    expect(result.current.user).toBeNull();
    expect(mockPush).toHaveBeenCalledWith("/login");
  });

  // The regression these guard: any non-401 answer used to be read as a logout,
  // so a restarting or saturated backend evicted a perfectly valid session.
  describe("transient backend failures", () => {
    it("does not log out when /auth/me answers 500", async () => {
      vi.spyOn(global, "fetch").mockResolvedValue(new Response(null, { status: 500 }));

      const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() });

      await waitFor(() => {
        expect(result.current.status).toBe("unavailable");
      });

      expect(result.current.isDegraded).toBe(true);
      expect(mockPush).not.toHaveBeenCalled();
    });

    it("keeps an established session when a later probe fails", async () => {
      vi.spyOn(global, "fetch")
        .mockResolvedValueOnce(
          new Response(JSON.stringify(mockUser()), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          })
        )
        .mockResolvedValue(new Response(null, { status: 503 }));

      const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() });

      await waitFor(() => {
        expect(result.current.isAuthenticated).toBe(true);
      });

      await act(async () => {
        await result.current.refreshUser();
      });

      expect(result.current.status).toBe("unavailable");
      expect(result.current.user?.username).toBe("admin");
      expect(mockPush).not.toHaveBeenCalled();
    });

    it("does not log out when the network call throws", async () => {
      vi.spyOn(global, "fetch").mockRejectedValue(new TypeError("fetch failed"));

      const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() });

      await waitFor(() => {
        expect(result.current.status).toBe("unavailable");
      });

      expect(mockPush).not.toHaveBeenCalled();
    });

    it("treats a 429 on refresh as retryable, not as a logout", async () => {
      vi.spyOn(global, "fetch")
        .mockResolvedValueOnce(new Response(null, { status: 401 }))
        .mockResolvedValue(new Response(null, { status: 429 }));

      const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() });

      await waitFor(() => {
        expect(result.current.status).toBe("unavailable");
      });

      expect(result.current.status).not.toBe("unauthenticated");
      expect(mockPush).not.toHaveBeenCalled();
    });

    it("still reports a real logout when refresh answers 401", async () => {
      vi.spyOn(global, "fetch").mockResolvedValue(new Response(null, { status: 401 }));

      const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() });

      await waitFor(() => {
        expect(result.current.status).toBe("unauthenticated");
      });

      expect(result.current.user).toBeNull();
    });
  });

  /**
   * `handleUnauthorized` used to live here: declared on the interface, defined
   * in the hook, returned from it — and called by nothing. It was also absent
   * from `AuthContextValue`, so no component could even reach it. Every 401
   * the app hit went unhandled with a refresh routine one unreachable call
   * away. It is replaced by a subscription the data-fetch layer really uses.
   */
  describe("a data request that could not be authorized", () => {
    it("re-probes /auth/me instead of trusting that route's status", async () => {
      resetSessionRevalidationThrottle();
      // A fresh Response per call: a body can only be read once, so a shared
      // instance would make the second probe fail to parse.
      const fetchMock = vi.spyOn(global, "fetch").mockImplementation(
        async () =>
          new Response(JSON.stringify(mockUser()), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
      );

      const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() });
      await waitFor(() => expect(result.current.isAuthenticated).toBe(true));

      const probesBefore = fetchMock.mock.calls.filter(
        ([url]) => url === "/api/auth/me",
      ).length;

      await act(async () => {
        requestSessionRevalidation();
      });

      await waitFor(() => {
        const probesAfter = fetchMock.mock.calls.filter(
          ([url]) => url === "/api/auth/me",
        ).length;
        expect(probesAfter).toBe(probesBefore + 1);
      });

      // A data route's 401 is not authoritative, so the session survives when
      // /auth/me says the user is still signed in.
      expect(result.current.status).toBe("authenticated");
      expect(mockPush).not.toHaveBeenCalled();
    });

    it("becomes a real logout only when /auth/me confirms it", async () => {
      resetSessionRevalidationThrottle();
      vi.spyOn(global, "fetch").mockImplementation(async (input) => {
        const url = String(input);
        if (url === "/api/auth/me") return new Response(null, { status: 401 });
        return new Response(null, { status: 401 }); // refresh refused too
      });

      const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() });
      await waitFor(() => expect(result.current.status).toBe("unauthenticated"));

      expect(result.current.user).toBeNull();
    });
  });

  it("collapses concurrent refreshes onto a single request", async () => {
    const fetchMock = vi
      .spyOn(global, "fetch")
      .mockResolvedValue(new Response(null, { status: 200 }));

    const [a, b, c] = await Promise.all([
      refreshSession(),
      refreshSession(),
      refreshSession(),
    ]);

    expect([a, b, c]).toEqual(["refreshed", "refreshed", "refreshed"]);
    // Rotation is single-use: a second in-flight POST would present an
    // already-rotated token and come back 401.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
