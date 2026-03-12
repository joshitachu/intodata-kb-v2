import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// Mock next/navigation before any imports that use it
const mockPush = vi.fn();
const mockRefresh = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, refresh: mockRefresh }),
}));

import { renderHook, waitFor, act } from "@testing-library/react";
import { useAuth } from "@/hooks/useAuth";

describe("useAuth", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("starts in loading state", () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      new Response(null, { status: 401 })
    );

    const { result } = renderHook(() => useAuth());
    expect(result.current.isLoading).toBe(true);
    expect(result.current.user).toBeNull();
  });

  it("sets user on successful auth", async () => {
    const mockUser = {
      id: 1,
      username: "admin",
      email: "admin@test.com",
      is_active: true,
      is_superadmin: true,
      roles: ["admin"],
      permissions: ["manage_users"],
    };

    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(mockUser), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );

    const { result } = renderHook(() => useAuth());

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.isAuthenticated).toBe(true);
    expect(result.current.user?.username).toBe("admin");
  });

  it("sets null user on 401", async () => {
    vi.spyOn(global, "fetch").mockResolvedValueOnce(
      new Response(null, { status: 401 })
    );

    const { result } = renderHook(() => useAuth());

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.isAuthenticated).toBe(false);
    expect(result.current.user).toBeNull();
  });

  it("logout clears user and redirects to login", async () => {
    const mockUser = {
      id: 1,
      username: "admin",
      email: "admin@test.com",
      is_active: true,
      is_superadmin: true,
      roles: [],
      permissions: [],
    };

    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify(mockUser), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      )
      .mockResolvedValueOnce(new Response(null, { status: 200 }));

    const { result } = renderHook(() => useAuth());

    await waitFor(() => {
      expect(result.current.isAuthenticated).toBe(true);
    });

    await act(async () => {
      await result.current.logout();
    });

    expect(result.current.user).toBeNull();
    expect(mockPush).toHaveBeenCalledWith("/login");
  });
});
