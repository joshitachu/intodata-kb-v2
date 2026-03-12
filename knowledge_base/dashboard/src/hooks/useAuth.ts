import { useEffect, useState } from "react";

function getLoginUrl(): string {
  // Configurable via Vite env var; auto-detects frontend on port 3000 otherwise
  if (import.meta.env.VITE_LOGIN_URL) {
    return import.meta.env.VITE_LOGIN_URL;
  }
  return `${window.location.protocol}//${window.location.hostname}/login`;
}

export interface AuthUser {
  id: number;
  username: string;
  is_superadmin: boolean;
  permissions: string[];
}

interface AuthState {
  authenticated: boolean;
  loading: boolean;
  user: AuthUser | null;
  error: string | null;
}

export function useAuth(): AuthState {
  const [state, setState] = useState<AuthState>({
    authenticated: false,
    loading: true,
    user: null,
    error: null,
  });

  useEffect(() => {
    async function verify() {
      try {
        // Uses /auth/ proxy (nginx in Docker, vite proxy in dev)
        const res = await fetch("/auth/me", {
          credentials: "include",
        });
        if (res.ok) {
          const data = await res.json();
          setState({
            authenticated: true,
            loading: false,
            user: {
              id: data.id,
              username: data.username,
              is_superadmin: data.is_superadmin,
              permissions: data.permissions ?? [],
            },
            error: null,
          });
        } else {
          const loginUrl = getLoginUrl();
          window.location.href = `${loginUrl}?redirect=${encodeURIComponent(window.location.href)}`;
        }
      } catch (err) {
        // Show error instead of silently redirecting on network failures
        setState({
          authenticated: false,
          loading: false,
          user: null,
          error: err instanceof Error ? err.message : "Authentication failed — check network connection",
        });
      }
    }
    verify();
  }, []);

  return state;
}
