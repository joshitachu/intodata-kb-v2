"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuthContext } from "@/components/AuthProvider";
import { TenantMark } from "@/components/TenantMark";
import { useTenantContext } from "@/contexts/TenantContext";

type Mode = "loading" | "login" | "code" | "mfa" | "bootstrap";
type AuthTab = "signin" | "signup";
type SignInMethod = "otp" | "password";

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const redirect = searchParams.get("redirect") || "/";
  const { loginAs } = useAuthContext();
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

  function finishLogin(data: { user?: { username: string; mfa_enabled?: boolean } & Record<string, unknown> }) {
    if (data.user) loginAs(data.user as unknown as Parameters<typeof loginAs>[0]);
    if (data.user?.mfa_enabled === false) {
      router.push("/settings/security");
      return;
    }
    if (redirect.startsWith("http")) {
      window.location.href = redirect;
    } else {
      router.push(redirect);
    }
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
      finishLogin(data);
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
      finishLogin(data);
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
      finishLogin(data);
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
      finishLogin(data);
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
      <div className="bg-white rounded-lg shadow-md p-8 text-center text-gray-500">
        Loading…
      </div>
    );
  }

  if (mode === "bootstrap") {
    return (
      <div className="bg-white rounded-lg shadow-md p-8">
        <div className="flex flex-col items-center mb-6">
          <TenantMark size="lg" />
          <p className="text-sm text-gray-500 text-center mt-3">
            Create the first admin for {tenant.name}
          </p>
          <p className="text-xs text-gray-400 text-center mt-1 max-w-xs">
            This endpoint disappears after the first user is created.
          </p>
        </div>
        <form onSubmit={handleBootstrap} className="space-y-4">
          <div>
            <label htmlFor="username" className="block text-sm font-medium text-gray-700 mb-1">
              Username
            </label>
            <input
              id="username"
              type="text"
              minLength={3}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-ring focus:border-ring"
              required
              autoFocus
              autoComplete="username"
            />
          </div>
          <div>
            <label htmlFor="email" className="block text-sm font-medium text-gray-700 mb-1">
              Email
            </label>
            <input
              id="email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-ring focus:border-ring"
              required
              autoComplete="email"
            />
          </div>
          <div>
            <label htmlFor="password" className="block text-sm font-medium text-gray-700 mb-1">
              Password (min 12 chars)
            </label>
            <input
              id="password"
              type="password"
              minLength={12}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-ring focus:border-ring"
              required
              autoComplete="new-password"
            />
          </div>
          {error && (
            <div className="text-sm text-red-600 bg-red-50 p-3 rounded-md">{error}</div>
          )}
          <button
            type="submit"
            disabled={loading}
            className="w-full py-2 px-4 bg-primary text-primary-foreground font-medium rounded-md hover:bg-primary/90 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? "Creating admin…" : "Create admin & continue"}
          </button>
        </form>
      </div>
    );
  }

  if (mode === "mfa") {
    return (
      <div className="bg-white rounded-lg shadow-md p-8">
        <div className="flex flex-col items-center mb-6">
          <TenantMark size="lg" />
          <p className="text-sm text-gray-500 text-center mt-3">
            Enter your 6-digit authenticator code
          </p>
          <p className="text-xs text-gray-400 text-center mt-1">
            Or use one of your backup codes
          </p>
        </div>
        <form onSubmit={handleVerifyMfa} className="space-y-4">
          <div>
            <label htmlFor="mfa-code" className="block text-sm font-medium text-gray-700 mb-1">
              Code
            </label>
            <input
              id="mfa-code"
              type="text"
              inputMode="numeric"
              value={mfaCode}
              onChange={(e) => setMfaCode(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-ring focus:border-ring tracking-widest text-center font-mono"
              required
              autoFocus
              autoComplete="one-time-code"
            />
          </div>
          {error && (
            <div className="text-sm text-red-600 bg-red-50 p-3 rounded-md">{error}</div>
          )}
          <button
            type="submit"
            disabled={loading}
            className="w-full py-2 px-4 bg-primary text-primary-foreground font-medium rounded-md hover:bg-primary/90 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? "Verifying…" : "Verify"}
          </button>
          <button
            type="button"
            onClick={() => {
              setMode("login");
              setMfaCode("");
              setMfaToken("");
            }}
            className="w-full text-sm text-gray-500 hover:text-gray-700"
          >
            Back to sign in
          </button>
        </form>
      </div>
    );
  }

  if (mode === "code") {
    return (
      <div className="bg-white rounded-lg shadow-md p-8">
        <div className="flex flex-col items-center mb-6">
          <TenantMark size="lg" />
          <p className="text-sm text-gray-500 text-center mt-3">
            Enter the code sent to {email}
          </p>
        </div>
        <form onSubmit={handleVerifyLoginCode} className="space-y-4">
          <div>
            <label htmlFor="otp-code" className="block text-sm font-medium text-gray-700 mb-1">
              Verification code
            </label>
            <input
              id="otp-code"
              type="text"
              inputMode="numeric"
              pattern="[0-9]{6}"
              maxLength={6}
              value={otpCode}
              onChange={(e) => setOtpCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
              className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-ring focus:border-ring tracking-widest text-center font-mono"
              required
              autoFocus
              autoComplete="one-time-code"
            />
          </div>
          {error && (
            <div className="text-sm text-red-600 bg-red-50 p-3 rounded-md">{error}</div>
          )}
          <button
            type="submit"
            disabled={loading || otpCode.length !== 6}
            className="w-full py-2 px-4 bg-primary text-primary-foreground font-medium rounded-md hover:bg-primary/90 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? "Verifying..." : "Continue"}
          </button>
          <button
            type="button"
            onClick={() => handleSendLoginCode()}
            disabled={loading}
            className="w-full text-sm text-gray-500 hover:text-gray-700 disabled:opacity-50"
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
            className="w-full text-sm text-gray-500 hover:text-gray-700"
          >
            Use a different email
          </button>
        </form>
      </div>
    );
  }

  return (
    <div className="bg-white rounded-lg shadow-md p-8">
      <div className="flex flex-col items-center mb-6">
        <TenantMark size="lg" />
        <p className="text-sm text-gray-500 text-center mt-3">
          Access {tenant.name}
        </p>
      </div>

      <div className="mb-6 grid grid-cols-2 rounded-md bg-gray-100 p-1">
        <button
          type="button"
          onClick={() => {
            setActiveTab("signin");
            setError("");
            setAccessMessage("");
          }}
          className={`rounded px-3 py-2 text-sm font-semibold transition ${
            activeTab === "signin"
              ? "bg-white text-gray-950 shadow-sm"
              : "text-gray-500 hover:text-gray-800"
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
          className={`rounded px-3 py-2 text-sm font-semibold transition ${
            activeTab === "signup"
              ? "bg-white text-gray-950 shadow-sm"
              : "text-gray-500 hover:text-gray-800"
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
                <label htmlFor="signin-email" className="block text-sm font-medium text-gray-700 mb-1">
                  Email
                </label>
                <input
                  id="signin-email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-ring focus:border-ring"
                  required
                  autoFocus
                  autoComplete="email"
                />
              </div>
              {error && (
                <div className="text-sm text-red-600 bg-red-50 p-3 rounded-md">{error}</div>
              )}
              <button
                type="submit"
                disabled={loading}
                className="w-full py-2 px-4 bg-primary text-primary-foreground font-medium rounded-md hover:bg-primary/90 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {loading ? "Sending code..." : "Email me a sign-in code"}
              </button>
              <button
                type="button"
                onClick={() => {
                  setSignInMethod("password");
                  setError("");
                }}
                className="w-full text-sm text-gray-500 hover:text-gray-700"
              >
                Use password instead
              </button>
            </form>
          ) : (
            <form onSubmit={handlePasswordLogin} className="space-y-4">
              <div>
                <label htmlFor="signin-identifier" className="block text-sm font-medium text-gray-700 mb-1">
                  Username or email
                </label>
                <input
                  id="signin-identifier"
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-ring focus:border-ring"
                  required
                  autoFocus
                  autoComplete="username"
                />
              </div>
              <div>
                <label htmlFor="signin-password" className="block text-sm font-medium text-gray-700 mb-1">
                  Password
                </label>
                <input
                  id="signin-password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-ring focus:border-ring"
                  required
                  autoComplete="current-password"
                />
              </div>
              {error && (
                <div className="text-sm text-red-600 bg-red-50 p-3 rounded-md">{error}</div>
              )}
              <button
                type="submit"
                disabled={loading}
                className="w-full py-2 px-4 bg-primary text-primary-foreground font-medium rounded-md hover:bg-primary/90 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {loading ? "Signing in..." : "Sign in with password"}
              </button>
              <button
                type="button"
                onClick={() => {
                  setSignInMethod("otp");
                  setError("");
                }}
                className="w-full text-sm text-gray-500 hover:text-gray-700"
              >
                Use email code instead
              </button>
            </form>
          )}
          <a
            href="/onboarding"
            className="block text-center text-sm text-gray-500 hover:text-gray-700"
          >
            Have a setup code? Finish onboarding
          </a>
        </div>
      ) : accessSubmitted ? (
        <div className="space-y-4">
          <div className="text-sm text-gray-700 bg-gray-50 p-3 rounded-md">
            Request sent. An admin needs to approve this before you can create an account.
          </div>
          {accessMessage && accessMessage !== "Request sent. An admin needs to approve this before you can create an account." && (
            <div className="text-sm text-gray-500">{accessMessage}</div>
          )}
          <button
            type="button"
            onClick={() => {
              setActiveTab("signin");
              setAccessSubmitted(false);
              setAccessMessage("");
            }}
            className="w-full py-2 px-4 bg-primary text-primary-foreground font-medium rounded-md hover:bg-primary/90 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
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
            className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-ring focus:border-ring"
          />
          <input
            type="email"
            value={accessEmail}
            onChange={(e) => setAccessEmail(e.target.value)}
            placeholder="Work email"
            className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-ring focus:border-ring"
            required
          />
          {accessMessage && (
            <div className="text-sm text-gray-700 bg-gray-50 p-3 rounded-md">{accessMessage}</div>
          )}
          <button
            type="submit"
            disabled={accessLoading}
            className="w-full py-2 px-4 bg-primary text-primary-foreground font-medium rounded-md hover:bg-primary/90 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {accessLoading ? "Submitting..." : "Request access"}
          </button>
        </form>
      )}
    </div>
  );
}

export default function LoginPage() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 p-4">
      <div className="w-full max-w-sm">
        <Suspense fallback={<div className="text-center text-gray-500">Loading...</div>}>
          <LoginForm />
        </Suspense>
      </div>
    </div>
  );
}
