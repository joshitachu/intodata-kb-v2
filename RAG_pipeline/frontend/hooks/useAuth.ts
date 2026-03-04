"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

interface AuthUser {
  id: number;
  username: string;
  email: string;
  is_active: boolean;
  is_superadmin: boolean;
  roles: string[];
}

interface UseAuthReturn {
  user: AuthUser | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  logout: () => Promise<void>;
}

export function useAuth(): UseAuthReturn {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const router = useRouter();

  const fetchUser = useCallback(async () => {
    try {
      const res = await fetch("/api/auth/me");
      if (res.ok) {
        const data = await res.json();
        setUser(data);
      } else {
        setUser(null);
      }
    } catch {
      setUser(null);
    } finally {
      setIsLoading(false);
    }
  }, []);

  const logout = useCallback(async () => {
    await fetch("/api/auth/logout", { method: "POST" });
    setUser(null);
    router.push("/login");
    router.refresh();
  }, [router]);

  // Fetch user on mount
  useEffect(() => {
    fetchUser();
  }, [fetchUser]);

  // Set up periodic token refresh (every 12 minutes, before 15-min expiry)
  useEffect(() => {
    if (!user) return;

    const interval = setInterval(
      async () => {
        const res = await fetch("/api/auth/refresh", { method: "POST" });
        if (!res.ok) {
          setUser(null);
          router.push("/login");
          router.refresh();
        }
      },
      12 * 60 * 1000,
    );

    return () => clearInterval(interval);
  }, [user, router]);

  return {
    user,
    isLoading,
    isAuthenticated: user !== null,
    logout,
  };
}
