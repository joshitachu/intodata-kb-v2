"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuthContext } from "@/components/AuthProvider";
import { useTenantContext } from "@/contexts/TenantContext";
import { AuthUserSchema } from "@/types";

type Mode = "loading" | "login" | "code" | "mfa" | "bootstrap";
type AuthTab = "signin" | "signup";
type SignInMethod = "otp" | "password";

// Shared token-driven styles so every auth mode looks identical and adapts to dark mode.
const inputCls =
  "w-full rounded-lg border border-input bg-background px-3 py-2.5 text-sm text-foreground placeholder:text-muted-foreground shadow-elevation-xs transition focus:outline-none focus:ring-2 focus:ring-ring focus:border-ring";
const codeInputCls = `${inputCls} text-center font-mono tracking-[0.4em]`;
const labelCls = "block text-sm font-medium text-foreground/80 mb-1.5";
const primaryBtn =
  "w-full rounded-lg bg-primary py-2.5 px-4 text-sm font-semibold text-primary-foreground shadow-elevation-xs transition hover:bg-primary/90 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 focus:ring-offset-background disabled:opacity-50 disabled:cursor-not-allowed";
const linkBtn =
  "w-full text-sm text-muted-foreground transition hover:text-foreground disabled:opacity-50";
const errorBox =
  "text-sm text-destructive bg-destructive/10 border border-destructive/20 px-3 py-2.5 rounded-lg";
const noteBox = "text-sm text-foreground/80 bg-muted px-3 py-2.5 rounded-lg";

function AuthCard({ children }: { children: React.ReactNode }) {
  return (
    <div className="animate-hero-enter rounded-2xl border bg-card p-7 shadow-elevation-lg">
      {children}
    </div>
  );
}

function AuthHeader({
  subtitle,
  hint,
}: {
  subtitle?: string;
  hint?: string;
}) {
  return (
    <div className="mb-7 flex flex-col items-center text-center">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src="/intodata-logo-hero.png"
        alt="Intodata"
        className="h-9 w-auto dark:brightness-0 dark:invert"
      />
      {subtitle && (
        <p className="mt-4 text-sm text-muted-foreground">{subtitle}</p>
      )}
      {hint && (
        <p className="mt-1 max-w-xs text-xs text-muted-foreground/80">{hint}</p>
      )}
    </div>
  );
}

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const redirect = searchParams.get("redirect") || "/";
  const { loginAs, refreshUser } = useAuthContext();
  const { tenant } = useTenantContext();

  const [mode, setMode] = useState<Mode>("loading");
  const [activeTab, setActiveTab] = useState<AuthTab>("signin");
  const [signInMethod, setSignInMethod] = useState<SignInMethod>("otp");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [email, setEmail] = useState("");
  const [otpCode, setOtpCode] = useState("");
  const [mfaToken, setMfaToken] = useState("");
  const [mfaCode, setMfaCode] = useState("");
  const [accessName, setAccessName] = useState("");
  const [accessEmail, setAccessEmail] = useState("");
  const [accessMessage, setAccessMessage] = useState("");
  const [accessSubmitted, setAccessSubmitted] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [accessLoading, setAccessLoading] = useState(false);

  // On mount, check if this is a fresh stack that needs bootstrap
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/auth/bootstrap-status", { cache: "no-store" });
        if (!res.ok) {
          if (!cancelled) setMode("login");
          return;
        }
        const data = await res.json();
        if (!cancelled) setMode(data.needs_bootstrap ? "bootstrap" : "login");
      } catch {
        if (!cancelled) setMode("login");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function finishLogin(data: unknown) {
    const user = data !== null && typeof data === "object" ? (data as Record<string, unknown>).user : undefined;
    const parsed = AuthUserSchema.safeParse(user);
    let authUser: Parameters<typeof loginAs>[0] | null = null;
    if (parsed.success) {
      authUser = parsed.data as Parameters<typeof loginAs>[0];
      loginAs(authUser);
    } else {
      authUser = await refreshUser();
      if (!authUser) {
        setError("Login succeeded, but your session could not be loaded. Please try again.");
        return;
      }
    }
    if (!authUser.mfa_enabled) {
      router.push("/settings/security");
      return;
    }
    const safeRedirect = redirect.startsWith("/") && !redirect.startsWith("//") ? redirect : "/";
    router.push(safeRedirect);
  }

  async function handleSendLoginCode(e?: React.FormEvent) {
    e?.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await fetch("/api/auth/otp/send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, purpose: "login" }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.status === 429) {
        setError("Too many attempts. Please wait a moment and try again.");
        return;
      }
      if (!res.ok) {
        setError(data?.error || "Could not send a sign-in code.");
        return;
      }
      setMode("code");
      setOtpCode("");
    } catch {
      setError("Network error. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  async function handleVerifyLoginCode(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await fetch("/api/auth/otp/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, code: otpCode, purpose: "login" }),
      });
      if (res.status === 429) {
        setError("Too many attempts. Please wait a moment and try again.");
        return;
      }
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data?.error || "Invalid or expired code");
        return;
      }
      if (data.mfa_required && data.mfa_token) {
        setMfaToken(data.mfa_token);
        setMode("mfa");
        return;
      }
      await finishLogin(data);
    } catch {
      setError("Network error. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  async function handlePasswordLogin(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ identifier: username, password }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.status === 429) {
        setError("Too many attempts. Please wait a moment and try again.");
        return;
      }
      if (!res.ok) {
        setError(data?.error || "Invalid username/email or password");
        return;
      }
      if (data.mfa_required && data.mfa_token) {
        setMfaToken(data.mfa_token);
        setMode("mfa");
        return;
      }
      await finishLogin(data);
    } catch {
      setError("Network error. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  async function handleVerifyMfa(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await fetch("/api/auth/verify-mfa", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mfa_token: mfaToken, code: mfaCode }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data?.error || "Invalid code");
        return;
      }
      await finishLogin(data);
    } catch {
      setError("Network error. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  async function handleBootstrap(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (password.length < 12) {
      setError("Password must be at least 12 characters.");
      return;
    }
    setLoading(true);
    try {
      const res = await fetch("/api/auth/bootstrap-admin", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, email, password }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data?.error || "Bootstrap failed");
        return;
      }
      await finishLogin(data);
    } catch {
      setError("Network error. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  async function handleRequestAccess(e: React.FormEvent) {
    e.preventDefault();
    setAccessMessage("");
    setError("");
    if (!accessEmail.includes("@")) {
      setAccessMessage("Enter a valid email address.");
      return;
    }
    setAccessLoading(true);
    try {
      const res = await fetch("/api/auth/request-access", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: accessEmail, full_name: accessName || null }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setAccessMessage(data?.error || "Request could not be submitted.");
        return;
      }
      setAccessMessage(
        data.message || "Request sent. An admin needs to approve this before you can create an account.",
      );
      if (data.status === "submitted" || data.status === "already_pending") {
        setAccessSubmitted(true);
        setAccessEmail("");
        setAccessName("");
      }
    } catch {
      setAccessMessage("Network error. Please try again.");
    } finally {
      setAccessLoading(false);
    }
  }

  if (mode === "loading") {
    return (
      <AuthCard>
        <div className="flex flex-col items-center gap-4 py-6 text-center">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary/30 border-t-primary" />
          <p className="text-sm text-muted-foreground">Loading…</p>
        </div>
      </AuthCard>
    );
  }

  if (mode === "bootstrap") {
    return (
      <AuthCard>
        <AuthHeader
          subtitle={`Create the first admin for ${tenant.name}`}
          hint="This endpoint disappears after the first user is created."
        />
        <form onSubmit={handleBootstrap} className="space-y-4">
          <div>
            <label htmlFor="username" className={labelCls}>Username</label>
            <input
              id="username"
              type="text"
              minLength={3}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className={inputCls}
              required
              autoFocus
              autoComplete="username"
            />
          </div>
          <div>
            <label htmlFor="email" className={labelCls}>Email</label>
            <input
              id="email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className={inputCls}
              required
              autoComplete="email"
            />
          </div>
          <div>
            <label htmlFor="password" className={labelCls}>Password (min 12 chars)</label>
            <input
              id="password"
              type="password"
              minLength={12}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className={inputCls}
              required
              autoComplete="new-password"
            />
          </div>
          {error && <div className={errorBox}>{error}</div>}
          <button type="submit" disabled={loading} className={primaryBtn}>
            {loading ? "Creating admin…" : "Create admin & continue"}
          </button>
        </form>
      </AuthCard>
    );
  }

  if (mode === "mfa") {
    return (
      <AuthCard>
        <AuthHeader
          subtitle="Enter your 6-digit authenticator code"
          hint="Or use one of your backup codes"
        />
        <form onSubmit={handleVerifyMfa} className="space-y-4">
          <div>
            <label htmlFor="mfa-code" className={labelCls}>Code</label>
            <input
              id="mfa-code"
              type="text"
              inputMode="numeric"
              value={mfaCode}
              onChange={(e) => setMfaCode(e.target.value)}
              className={codeInputCls}
              required
              autoFocus
              autoComplete="one-time-code"
            />
          </div>
          {error && <div className={errorBox}>{error}</div>}
          <button type="submit" disabled={loading} className={primaryBtn}>
            {loading ? "Verifying…" : "Verify"}
          </button>
          <button
            type="button"
            onClick={() => {
              setMode("login");
              setMfaCode("");
              setMfaToken("");
            }}
            className={linkBtn}
          >
            Back to sign in
          </button>
        </form>
      </AuthCard>
    );
  }

  if (mode === "code") {
    return (
      <AuthCard>
        <AuthHeader subtitle={`Enter the code sent to ${email}`} />
        <form onSubmit={handleVerifyLoginCode} className="space-y-4">
          <div>
            <label htmlFor="otp-code" className={labelCls}>Verification code</label>
            <input
              id="otp-code"
              type="text"
              inputMode="numeric"
              pattern="[0-9]{6}"
              maxLength={6}
              value={otpCode}
              onChange={(e) => setOtpCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
              className={codeInputCls}
              required
              autoFocus
              autoComplete="one-time-code"
            />
          </div>
          {error && <div className={errorBox}>{error}</div>}
          <button
            type="submit"
            disabled={loading || otpCode.length !== 6}
            className={primaryBtn}
          >
            {loading ? "Verifying..." : "Continue"}
          </button>
          <button
            type="button"
            onClick={() => handleSendLoginCode()}
            disabled={loading}
            className={linkBtn}
          >
            Resend email
          </button>
          <button
            type="button"
            onClick={() => {
              setMode("login");
              setOtpCode("");
              setError("");
            }}
            className={linkBtn}
          >
            Use a different email
          </button>
        </form>
      </AuthCard>
    );
  }

  return (
    <AuthCard>
      <AuthHeader subtitle={`Access ${tenant.name}`} />

      <div className="mb-6 grid grid-cols-2 gap-1 rounded-lg bg-muted p-1">
        <button
          type="button"
          onClick={() => {
            setActiveTab("signin");
            setError("");
            setAccessMessage("");
          }}
          className={`rounded-md px-3 py-2 text-sm font-semibold transition ${
            activeTab === "signin"
              ? "bg-background text-foreground shadow-elevation-xs"
              : "text-muted-foreground hover:text-foreground"
          }`}
        >
          Sign in
        </button>
        <button
          type="button"
          onClick={() => {
            setActiveTab("signup");
            setError("");
            setAccessMessage("");
            setAccessSubmitted(false);
          }}
          className={`rounded-md px-3 py-2 text-sm font-semibold transition ${
            activeTab === "signup"
              ? "bg-background text-foreground shadow-elevation-xs"
              : "text-muted-foreground hover:text-foreground"
          }`}
        >
          Request access
        </button>
      </div>

      {activeTab === "signin" ? (
        <div className="space-y-4">
          {signInMethod === "otp" ? (
            <form onSubmit={handleSendLoginCode} className="space-y-4">
              <div>
                <label htmlFor="signin-email" className={labelCls}>Email</label>
                <input
                  id="signin-email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className={inputCls}
                  required
                  autoFocus
                  autoComplete="email"
                />
              </div>
              {error && <div className={errorBox}>{error}</div>}
              <button type="submit" disabled={loading} className={primaryBtn}>
                {loading ? "Sending code..." : "Email me a sign-in code"}
              </button>
              <button
                type="button"
                onClick={() => {
                  setSignInMethod("password");
                  setError("");
                }}
                className={linkBtn}
              >
                Use password instead
              </button>
            </form>
          ) : (
            <form onSubmit={handlePasswordLogin} className="space-y-4">
              <div>
                <label htmlFor="signin-identifier" className={labelCls}>Username or email</label>
                <input
                  id="signin-identifier"
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  className={inputCls}
                  required
                  autoFocus
                  autoComplete="username"
                />
              </div>
              <div>
                <label htmlFor="signin-password" className={labelCls}>Password</label>
                <input
                  id="signin-password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className={inputCls}
                  required
                  autoComplete="current-password"
                />
              </div>
              {error && <div className={errorBox}>{error}</div>}
              <button type="submit" disabled={loading} className={primaryBtn}>
                {loading ? "Signing in..." : "Sign in with password"}
              </button>
              <button
                type="button"
                onClick={() => {
                  setSignInMethod("otp");
                  setError("");
                }}
                className={linkBtn}
              >
                Use email code instead
              </button>
            </form>
          )}
          <a
            href="/onboarding"
            className="block text-center text-sm text-muted-foreground transition hover:text-foreground"
          >
            Have a setup code? Finish onboarding
          </a>
        </div>
      ) : accessSubmitted ? (
        <div className="space-y-4">
          <div className={noteBox}>
            Request sent. An admin needs to approve this before you can create an account.
          </div>
          {accessMessage && accessMessage !== "Request sent. An admin needs to approve this before you can create an account." && (
            <div className="text-sm text-muted-foreground">{accessMessage}</div>
          )}
          <button
            type="button"
            onClick={() => {
              setActiveTab("signin");
              setAccessSubmitted(false);
              setAccessMessage("");
            }}
            className={primaryBtn}
          >
            Back to sign in
          </button>
        </div>
      ) : (
        <form onSubmit={handleRequestAccess} className="space-y-3">
          <input
            type="text"
            value={accessName}
            onChange={(e) => setAccessName(e.target.value)}
            placeholder="Full name"
            className={inputCls}
          />
          <input
            type="email"
            value={accessEmail}
            onChange={(e) => setAccessEmail(e.target.value)}
            placeholder="Work email"
            className={inputCls}
            required
          />
          {accessMessage && <div className={noteBox}>{accessMessage}</div>}
          <button type="submit" disabled={accessLoading} className={primaryBtn}>
            {accessLoading ? "Submitting..." : "Request access"}
          </button>
        </form>
      )}
    </AuthCard>
  );
}

function AuroraBackground() {
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
      <div
        className="absolute -top-1/3 left-1/2 h-[70vh] w-[70vh] -translate-x-1/2 rounded-full opacity-[0.10] dark:opacity-[0.18]"
        style={{
          background:
            "radial-gradient(circle at center, hsl(var(--primary)) 0%, transparent 70%)",
        }}
      />
      <div
        className="absolute -bottom-1/4 -right-1/4 h-[55vh] w-[55vh] rounded-full opacity-[0.07] dark:opacity-[0.12]"
        style={{
          background:
            "radial-gradient(circle at center, hsl(var(--primary)) 0%, transparent 70%)",
        }}
      />
    </div>
  );
}

export default function LoginPage() {
  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-background p-4">
      <AuroraBackground />
      <div className="relative w-full max-w-sm">
        <Suspense
          fallback={
            <div className="text-center text-sm text-muted-foreground">Loading...</div>
          }
        >
          <LoginForm />
        </Suspense>
        <p className="mt-6 text-center text-xs text-muted-foreground/70">
          Secured with encrypted sessions and optional two-factor authentication.
        </p>
      </div>
    </div>
  );
}
