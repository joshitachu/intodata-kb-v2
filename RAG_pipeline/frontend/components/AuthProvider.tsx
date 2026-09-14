"use client";

import {
  createContext,
  useContext,
  useEffect,
  type ReactNode,
} from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuth, type AuthStatus } from "@/hooks/useAuth";

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
  status: AuthStatus;
  isDegraded: boolean;
  logout: () => Promise<void>;
  loginAs: (user: AuthUser) => void;
  refreshUser: () => Promise<AuthUser | null>;
}

const AuthContext = createContext<AuthContextValue>({
  user: null,
  isLoading: true,
  isAuthenticated: false,
  status: "loading",
  isDegraded: false,
  logout: async () => {},
  loginAs: () => {},
  refreshUser: async () => null,
});

// Must match the page routes made public by middleware.ts. Middleware allows
// the initial request through; this list prevents the client-side 401 handler
// from undoing that decision once the application has hydrated.
const PUBLIC_PATHS = ["/login", "/onboarding", "/guide"];

export function AuthProvider({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    // Only a definitive 401 sends anyone to the login page. A missing answer
    // from the backend ("unavailable") keeps the session and retries; bouncing
    // on it logged users out of a valid session during any backend hiccup.
    if (auth.status !== "unauthenticated") return;
    if (PUBLIC_PATHS.some((path) => pathname.startsWith(path))) return;

    router.replace(`/login?redirect=${encodeURIComponent(pathname)}`);
  }, [auth.status, pathname, router]);

  useEffect(() => {
    if (auth.status !== "authenticated" || !auth.user) return;
    if (pathname === "/login" || pathname.startsWith("/onboarding")) return;

    if (!auth.user.mfa_enabled) {
      if (pathname !== "/settings/security") router.replace("/settings/security");
      return;
    }

    if (
      !auth.user.has_password &&
      !auth.user.sso_provider &&
      pathname !== "/settings/profile" &&
      pathname !== "/settings/security"
    ) {
      router.replace("/settings/profile");
    }
  }, [auth.status, auth.user, pathname, router]);

  // Cold start with an unreachable backend: we cannot tell whether the visitor
  // is signed in, so show a retry state rather than guessing "logged out".
  const isPublic = PUBLIC_PATHS.some((path) => pathname.startsWith(path));
  if (auth.status === "unavailable" && !auth.user && !isPublic) {
    return (
      <AuthContext.Provider value={auth}>
        <BackendUnreachable />
      </AuthContext.Provider>
    );
  }

  return <AuthContext.Provider value={auth}>{children}</AuthContext.Provider>;
}

function BackendUnreachable() {
  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <div className="max-w-sm rounded-2xl border bg-card p-6 text-center shadow-elevation-lg">
        <p className="text-sm font-medium text-foreground">Reconnecting…</p>
        <p className="mt-2 text-sm text-muted-foreground">
          The server is not responding. Your session is still valid — this will
          recover on its own.
        </p>
      </div>
    </div>
  );
}

export function useAuthContext() {
  return useContext(AuthContext);
}
