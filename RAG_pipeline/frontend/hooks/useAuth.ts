"use client";

import { useCallback, useEffect, useRef, useState } from "react";
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
  last_login_at: string | null;
  has_password: boolean;
  sso_provider: string | null;
}

/**
 * Auth state is a three-way outcome, never a boolean.
 *
 * The old two-way model ("we have a user" / "we don't") could not tell
 * "the server says you are logged out" apart from "the server did not answer",
 * so a backend restart, a queued request, or a shared rate-limit bucket logged
 * the user out while their session cookies were still perfectly valid.
 * Only `unauthenticated` may ever redirect to /login.
 */
export type AuthStatus = "loading" | "authenticated" | "unauthenticated" | "unavailable";

const REFRESH_INTERVAL_MS = 10 * 60 * 1000;
/** Backoff schedule used while the backend is unreachable. */
const RETRY_BACKOFF_MS = [2_000, 5_000, 10_000, 20_000, 30_000];
/** Don't revalidate on tab focus more than once per minute. */
const FOCUS_REVALIDATE_THROTTLE_MS = 60_000;

type RefreshOutcome = "refreshed" | "unauthenticated" | "unavailable";
type ProbeOutcome =
  | { kind: "authenticated"; user: AuthUser }
  | { kind: "unauthenticated" }
  | { kind: "unavailable" };

/**
 * Module-level so every caller in the tab shares one in-flight request.
 * Refresh-token rotation is single-use: two concurrent POSTs mean one of them
 * presents an already-rotated token and gets a spurious 401.
 */
let inFlightRefresh: Promise<RefreshOutcome> | null = null;

/**
 * 401 is the only status that means "logged out". Everything else — 429 from a
 * shared rate-limit bucket, 5xx from a restarting or saturated backend, 403
 * from an authorization rule on a perfectly valid session — leaves the session
 * intact and is retried.
 */
function classify(status: number): "unauthenticated" | "unavailable" {
  return status === 401 ? "unauthenticated" : "unavailable";
}

async function requestRefresh(): Promise<RefreshOutcome> {
  try {
    const res = await fetch("/api/auth/refresh", { method: "POST", cache: "no-store" });
    if (res.ok) return "refreshed";
    return classify(res.status);
  } catch {
    // Network-level failure: the answer is unknown, so assume nothing.
    return "unavailable";
  }
}

/** Rotate the refresh token, collapsing concurrent callers onto one request. */
export function refreshSession(): Promise<RefreshOutcome> {
  if (inFlightRefresh) return inFlightRefresh;
  inFlightRefresh = requestRefresh().finally(() => {
    inFlightRefresh = null;
  });
  return inFlightRefresh;
}

interface UseAuthReturn {
  user: AuthUser | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  /** Full auth state. Use this instead of `!user` before redirecting anywhere. */
  status: AuthStatus;
  /** True when the backend could not be reached; the session is NOT invalid. */
  isDegraded: boolean;
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
  const [status, setStatus] = useState<AuthStatus>("loading");
  const router = useRouter();
  const queryClient = useQueryClient();

  // Refs keep the callbacks and effects below stable: anything that closed over
  // `user` would change identity on every login and cancel the pending retry
  // timer or reset the refresh clock.
  const userRef = useRef<AuthUser | null>(null);
  const retryAttempt = useRef(0);
  const lastRevalidatedAt = useRef(0);

  const applyUser = useCallback((next: AuthUser | null, nextStatus: AuthStatus) => {
    userRef.current = next;
    setUser(next);
    setStatus(nextStatus);
  }, []);

  const probe = useCallback(async (): Promise<ProbeOutcome> => {
    let res: Response;
    try {
      res = await fetch("/api/auth/me", { cache: "no-store" });
    } catch {
      return { kind: "unavailable" };
    }

    if (res.status === 401) {
      const outcome = await refreshSession();
      if (outcome !== "refreshed") return { kind: outcome };
      try {
        res = await fetch("/api/auth/me", { cache: "no-store" });
      } catch {
        return { kind: "unavailable" };
      }
      // A 401 immediately after a successful rotation is a real logout.
      if (res.status === 401) return { kind: "unauthenticated" };
    }

    if (res.ok) {
      try {
        return { kind: "authenticated", user: (await res.json()) as AuthUser };
      } catch {
        return { kind: "unavailable" };
      }
    }

    return { kind: classify(res.status) };
  }, []);

  const fetchUser = useCallback(async (): Promise<AuthUser | null> => {
    const outcome = await probe();

    if (outcome.kind === "authenticated") {
      retryAttempt.current = 0;
      lastRevalidatedAt.current = Date.now();
      applyUser(outcome.user, "authenticated");
      return outcome.user;
    }

    if (outcome.kind === "unauthenticated") {
      retryAttempt.current = 0;
      applyUser(null, "unauthenticated");
      return null;
    }

    // Unavailable: hold on to whatever session we already have and retry.
    // Never clear the user here — that is what used to bounce people to /login.
    setStatus("unavailable");
    return userRef.current;
  }, [probe, applyUser]);

  const loginAs = useCallback(
    (u: AuthUser) => {
      // Drop any cache belonging to the previous session so onboarding status,
      // notifications, conversations etc. refetch against the new user.
      queryClient.clear();
      retryAttempt.current = 0;
      lastRevalidatedAt.current = Date.now();
      applyUser(u, "authenticated");
    },
    [queryClient, applyUser],
  );

  const logout = useCallback(async () => {
    try {
      await fetch("/api/auth/logout", { method: "POST" });
    } catch {
      // A failed logout call still ends the local session.
    }
    applyUser(null, "unauthenticated");
    queryClient.clear();
    router.push("/login");
    router.refresh();
  }, [router, queryClient, applyUser]);

  // Initial load.
  useEffect(() => {
    void fetchUser();
  }, [fetchUser]);

  // Intercept a 401 response: try refreshing the token once. Returns true when
  // the caller should retry. Only a definitive logout redirects; a transient
  // failure leaves the session alone so the caller can back off and retry.
  const handleUnauthorized = useCallback(async (): Promise<boolean> => {
    const outcome = await refreshSession();
    if (outcome === "refreshed") return true;
    if (outcome === "unavailable") {
      setStatus("unavailable");
      return false;
    }
    applyUser(null, "unauthenticated");
    router.push("/login");
    router.refresh();
    return false;
  }, [router, applyUser]);

  // Periodic token refresh, well before the 60-minute access token expiry.
  // A failure here must not end the session: only an explicit 401 does.
  useEffect(() => {
    if (status !== "authenticated") return;

    const interval = setInterval(() => {
      void (async () => {
        const outcome = await refreshSession();
        if (outcome === "unauthenticated") {
          applyUser(null, "unauthenticated");
        } else if (outcome === "unavailable") {
          setStatus("unavailable");
        }
      })();
    }, REFRESH_INTERVAL_MS);

    return () => clearInterval(interval);
  }, [status, applyUser]);

  // While the backend is unreachable, retry on a capped backoff instead of
  // treating the outage as a logout.
  useEffect(() => {
    if (status !== "unavailable") return;

    const delay = RETRY_BACKOFF_MS[Math.min(retryAttempt.current, RETRY_BACKOFF_MS.length - 1)];
    retryAttempt.current += 1;
    const timer = setTimeout(() => void fetchUser(), delay);

    return () => clearTimeout(timer);
  }, [status, fetchUser]);

  // Revalidate when the tab comes back into view. A suspended tab can sit past
  // the access-token expiry with its interval throttled; without this the first
  // action after waking would hit a 401 mid-flight.
  useEffect(() => {
    if (status === "unauthenticated" || status === "loading") return;

    function onVisible() {
      if (document.visibilityState !== "visible") return;
      if (Date.now() - lastRevalidatedAt.current < FOCUS_REVALIDATE_THROTTLE_MS) return;
      lastRevalidatedAt.current = Date.now();
      void fetchUser();
    }

    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [status, fetchUser]);

  return {
    user,
    isLoading: status === "loading",
    isAuthenticated: user !== null,
    status,
    isDegraded: status === "unavailable",
    logout,
    loginAs,
    refreshUser: fetchUser,
    handleUnauthorized,
  };
}
