"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuthContext } from "@/components/AuthProvider";
import { TenantMark } from "@/components/TenantMark";

type Step = "email" | "code" | "profile";

function defaultUsername(email: string) {
  return email.split("@")[0]?.replace(/[^a-zA-Z0-9_.-]/g, "") || "";
}

function stepHeading(step: Step) {
  if (step === "email") return "Account setup";
  if (step === "code") return "Enter your setup code";
  return "Complete your profile";
}

function InviteOnboarding() {
  const params = useSearchParams();
  const router = useRouter();
  const { user, isLoading: authLoading, loginAs } = useAuthContext();
  const legacyToken = params.get("token") || "";

  const [step, setStep] = useState<Step>("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [username, setUsername] = useState("");
  const [fullName, setFullName] = useState("");
  const [role, setRole] = useState("");
  const [intendedUse, setIntendedUse] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!authLoading && user) router.replace("/");
  }, [authLoading, user, router]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!legacyToken) return;
      setLoading(true);
      const res = await fetch(`/api/auth/invite/validate?token=${encodeURIComponent(legacyToken)}`);
      const data = await res.json().catch(() => ({}));
      if (cancelled) return;
      setLoading(false);
      if (!res.ok) {
        setError(data.error || "Invite is invalid or expired.");
        return;
      }
      const inviteEmail = String(data.email || "");
      setEmail(inviteEmail);
      setFullName(data.full_name || "");
      setUsername(defaultUsername(inviteEmail));
      setStep("profile");
    })();
    return () => {
      cancelled = true;
    };
  }, [legacyToken]);

  if (authLoading || user) return null;

  async function sendCode(e?: React.FormEvent) {
    e?.preventDefault();
    setError("");
    setMessage("");
    setLoading(true);
    try {
      const res = await fetch("/api/auth/invite/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.error || "Setup code could not be sent.");
        return;
      }
      setStep("code");
      setCode("");
      setMessage(data.message || "Setup code sent.");
    } catch {
      setError("Network error. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  async function verifyCode(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setMessage("");
    setLoading(true);
    try {
      const res = await fetch("/api/auth/otp/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, code, purpose: "invite" }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.error || "Invalid or expired code.");
        return;
      }
      setFullName(data.full_name || "");
      setUsername(defaultUsername(email));
      setStep("profile");
    } catch {
      setError("Network error. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await fetch("/api/auth/invite/accept", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          token: legacyToken || null,
          email: legacyToken ? null : email,
          code: legacyToken ? null : code,
          username,
          full_name: fullName,
          role,
          intended_use: intendedUse,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.error || "Invite could not be accepted.");
        return;
      }
      if (data.user) loginAs(data.user);
      router.push("/settings/security");
    } catch {
      setError("Network error. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="bg-white rounded-lg shadow-md p-8">
      <div className="flex flex-col items-center mb-6">
        <TenantMark size="lg" />
        <h1 className="text-xl font-semibold text-gray-950 mt-4">
          {stepHeading(step)}
        </h1>
        <p className="text-sm text-gray-500 text-center mt-3">
          {step === "email" && "Use the email address an admin approved for access."}
          {step === "code" && "Enter the setup code from your invitation email."}
          {step === "profile" && `${email} · profile details before security setup`}
        </p>
      </div>

      {step === "email" && (
        <form onSubmit={sendCode} className="space-y-4">
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="Work email"
            className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-ring focus:border-ring"
            required
            autoFocus
            autoComplete="email"
          />
          {error && <div className="text-sm text-red-600 bg-red-50 p-3 rounded-md">{error}</div>}
          <button
            type="submit"
            disabled={loading}
            className="w-full py-2 px-4 bg-primary text-primary-foreground font-medium rounded-md hover:bg-primary/90 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? "Sending code..." : "Continue to setup code"}
          </button>
        </form>
      )}

      {step === "code" && (
        <form onSubmit={verifyCode} className="space-y-4">
          {message && <div className="text-sm text-gray-700 bg-gray-50 p-3 rounded-md">{message}</div>}
          <input
            type="text"
            inputMode="numeric"
            pattern="[0-9]{6}"
            maxLength={6}
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
            placeholder="Verification code"
            className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-ring focus:border-ring tracking-widest text-center font-mono"
            required
            autoFocus
            autoComplete="one-time-code"
          />
          {error && <div className="text-sm text-red-600 bg-red-50 p-3 rounded-md">{error}</div>}
          <button
            type="submit"
            disabled={loading || code.length !== 6}
            className="w-full py-2 px-4 bg-primary text-primary-foreground font-medium rounded-md hover:bg-primary/90 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? "Verifying..." : "Continue to profile"}
          </button>
          <button
            type="button"
            onClick={() => sendCode()}
            disabled={loading}
            className="w-full text-sm text-gray-500 hover:text-gray-700 disabled:opacity-50"
          >
            Resend email
          </button>
        </form>
      )}

      {step === "profile" && (
        <form onSubmit={submit} className="space-y-4">
          <input
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            placeholder="Full name"
            className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-ring focus:border-ring"
            required
          />
          <input
            value={role}
            onChange={(e) => setRole(e.target.value)}
            placeholder="Role or title"
            className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-ring focus:border-ring"
            required
          />
          <textarea
            value={intendedUse}
            onChange={(e) => setIntendedUse(e.target.value)}
            placeholder="Intended platform use"
            rows={4}
            className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-ring focus:border-ring"
            required
          />
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="Username"
            className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-ring focus:border-ring"
            required
          />
          {error && <div className="text-sm text-red-600 bg-red-50 p-3 rounded-md">{error}</div>}
          <button
            type="submit"
            disabled={loading}
            className="w-full py-2 px-4 bg-primary text-primary-foreground font-medium rounded-md hover:bg-primary/90 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? "Creating account..." : "Create account and set up security"}
          </button>
        </form>
      )}
    </div>
  );
}

export default function OnboardingInvitePage() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 p-4">
      <div className="w-full max-w-md">
        <Suspense fallback={<div className="text-center text-gray-500">Loading...</div>}>
          <InviteOnboarding />
        </Suspense>
      </div>
    </div>
  );
}
