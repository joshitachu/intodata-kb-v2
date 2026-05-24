"use client";

import {
  createContext,
  useContext,
  useEffect,
  type ReactNode,
} from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/hooks/useAuth";

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
  last_login_at: string | null;
  has_password: boolean;
  sso_provider: string | null;
}

interface AuthContextValue {
  user: AuthUser | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  logout: () => Promise<void>;
  loginAs: (user: AuthUser) => void;
  refreshUser: () => Promise<AuthUser | null>;
}

const AuthContext = createContext<AuthContextValue>({
  user: null,
  isLoading: true,
  isAuthenticated: false,
  logout: async () => {},
  loginAs: () => {},
  refreshUser: async () => null,
});

const PUBLIC_PATHS = ["/login", "/onboarding"];

export function AuthProvider({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    if (auth.isLoading || auth.user) return;
    if (PUBLIC_PATHS.some((path) => pathname.startsWith(path))) return;

    router.replace(`/login?redirect=${encodeURIComponent(pathname)}`);
  }, [auth.isLoading, auth.user, pathname, router]);

  useEffect(() => {
    if (auth.isLoading || !auth.user) return;
    if (auth.user.mfa_enabled) return;
    if (pathname === "/login" || pathname === "/settings/security") return;

    router.replace("/settings/security");
  }, [auth.isLoading, auth.user, pathname, router]);

  return <AuthContext.Provider value={auth}>{children}</AuthContext.Provider>;
}

export function useAuthContext() {
  return useContext(AuthContext);
}
