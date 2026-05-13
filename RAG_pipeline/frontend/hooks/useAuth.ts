"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";

interface AuthUser {
  id: number;
  username: string;
  email: string;
  is_active: boolean;
  is_superadmin: boolean;
  roles: string[];
  permissions: string[];
  onboarding_completed: boolean;
  tour_completed: boolean;
  mfa_enabled: boolean;
}

interface UseAuthReturn {
  user: AuthUser | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  logout: () => Promise<void>;
  /** Set user state directly (e.g. after login). */
  loginAs: (user: AuthUser) => void;
  /** Refetch /auth/me and update local auth state. */
  refreshUser: () => Promise<AuthUser | null>;
  /** Attempt a token refresh on 401. Returns true if refreshed (caller should retry). */
  handleUnauthorized: () => Promise<boolean>;
}

export function useAuth(): UseAuthReturn {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const router = useRouter();
  const queryClient = useQueryClient();

  // Attempt a token refresh; returns true on success, false on failure.
  const tryRefresh = useCallback(async (): Promise<boolean> => {
    try {
      const res = await fetch("/api/auth/refresh", { method: "POST" });
      return res.ok;
    } catch {
      return false;
    }
  }, []);

  const fetchUser = useCallback(async (): Promise<AuthUser | null> => {
    try {
      let res = await fetch("/api/auth/me");
      if (res.status === 401) {
        const refreshed = await tryRefresh();
        if (refreshed) {
          res = await fetch("/api/auth/me");
        }
      }
      if (res.ok) {
        const data = await res.json();
        setUser(data);
        return data;
      } else {
        setUser(null);
        return null;
      }
    } catch {
      setUser(null);
      return null;
    } finally {
      setIsLoading(false);
    }
  }, [tryRefresh]);

  const loginAs = useCallback(
    (u: AuthUser) => {
      // Drop any cache belonging to the previous session so onboarding status,
      // notifications, conversations etc. refetch against the new user.
      queryClient.clear();
      setUser(u);
      setIsLoading(false);
    },
    [queryClient],
  );

  const logout = useCallback(async () => {
    await fetch("/api/auth/logout", { method: "POST" });
    setUser(null);
    queryClient.clear();
    router.push("/login");
    router.refresh();
  }, [router, queryClient]);

  // Fetch user on mount
  useEffect(() => {
    fetchUser();
  }, [fetchUser]);

  // Intercept a 401 response: try refreshing the token once.
  // If the refresh succeeds, returns true so the caller can retry.
  // If the refresh fails, redirects to login and returns false.
  const handleUnauthorized = useCallback(async (): Promise<boolean> => {
    const refreshed = await tryRefresh();
    if (refreshed) return true;
    setUser(null);
    router.push("/login");
    router.refresh();
    return false;
  }, [tryRefresh, router]);

  // Set up periodic token refresh (every 10 minutes, before 60-min expiry)
  useEffect(() => {
    if (!user) return;

    const interval = setInterval(
      async () => {
        const ok = await tryRefresh();
        if (!ok) {
          setUser(null);
          router.push("/login");
          router.refresh();
        }
      },
      10 * 60 * 1000,
    );

    return () => clearInterval(interval);
  }, [user, router, tryRefresh]);

  return {
    user,
    isLoading,
    isAuthenticated: user !== null,
    logout,
    loginAs,
    refreshUser: fetchUser,
    handleUnauthorized,
  };
}
