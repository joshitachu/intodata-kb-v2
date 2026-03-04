import { useEffect, useState } from "react";

function getLoginUrl(): string {
  // Configurable via Vite env var; auto-detects frontend on port 3000 otherwise
  if (import.meta.env.VITE_LOGIN_URL) {
    return import.meta.env.VITE_LOGIN_URL;
  }
  return `${window.location.protocol}//${window.location.hostname}:3000/login`;
}

interface AuthState {
  authenticated: boolean;
  loading: boolean;
}

export function useAuth(): AuthState {
  const [state, setState] = useState<AuthState>({
    authenticated: false,
    loading: true,
  });

  useEffect(() => {
    async function verify() {
      try {
        // Uses /auth/ proxy (nginx in Docker, vite proxy in dev)
        const res = await fetch("/auth/me", {
          credentials: "include",
        });
        if (res.ok) {
          setState({ authenticated: true, loading: false });
        } else {
          const loginUrl = getLoginUrl();
          window.location.href = `${loginUrl}?redirect=${encodeURIComponent(window.location.href)}`;
        }
      } catch {
        const loginUrl = getLoginUrl();
        window.location.href = `${loginUrl}?redirect=${encodeURIComponent(window.location.href)}`;
      }
    }
    verify();
  }, []);

  return state;
}
