"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { QRCodeSVG } from "qrcode.react";
import { useAuthContext } from "@/components/AuthProvider";
import { useOnboardingContext } from "@/contexts/OnboardingContext";
import { useCompleteOnboarding } from "@/hooks/useOnboarding";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { MobileTopBar } from "@/components/mobile/MobileTopBar";
import { useViewport } from "@/hooks/useViewport";
import { cn } from "@/lib/utils";

type SetupState =
  | { kind: "idle" }
  | { kind: "pending"; secret: string; otpauthUri: string }
  | { kind: "enabled"; backupCodes: string[] }
  | { kind: "error"; message: string };

export default function SecuritySettingsPage() {
  const router = useRouter();
  const { user, refreshUser } = useAuthContext();
  const { startTour } = useOnboardingContext();
  const completeOnboarding = useCompleteOnboarding();
  const mfaEnabled = user?.mfa_enabled ?? false;

  const [state, setState] = useState<SetupState>({ kind: "idle" });
  const [code, setCode] = useState("");
  const [disablePassword, setDisablePassword] = useState("");
  const [disableCode, setDisableCode] = useState("");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function startSetup() {
    setLoading(true);
    setMessage(null);
    try {
      const res = await fetch("/api/auth/totp/setup", { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        setState({ kind: "error", message: data.error || "Setup failed" });
        return;
      }
      setState({ kind: "pending", secret: data.secret, otpauthUri: data.otpauth_uri });
    } finally {
      setLoading(false);
    }
  }

  async function verifyCode() {
    if (state.kind !== "pending") return;
    setLoading(true);
    setMessage(null);
    try {
      const res = await fetch("/api/auth/totp/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      });
      const data = await res.json();
      if (!res.ok) {
        setMessage(data.error || "Invalid code");
        return;
      }
      setState({ kind: "enabled", backupCodes: data.backup_codes });
      setCode("");
    } finally {
      setLoading(false);
    }
  }

  async function finishRequiredSetup() {
    setLoading(true);
    setMessage(null);
    try {
      if (setupOnly) {
        await completeOnboarding.mutateAsync();
      }
      await refreshUser();
      if (setupOnly) {
        startTour();
        router.push("/");
      } else {
        setState({ kind: "idle" });
      }
    } catch {
      setMessage("Security setup finished, but the next step could not start. Please refresh and try again.");
    } finally {
      setLoading(false);
    }
  }

  async function disableMfa() {
    setLoading(true);
    setMessage(null);
    try {
      const res = await fetch("/api/auth/totp/disable", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: disablePassword, code: disableCode }),
      });
      const data = await res.json();
      if (!res.ok) {
        setMessage(data.error || "Disable failed");
        return;
      }
      setMessage("MFA disabled.");
      setState({ kind: "idle" });
      setDisablePassword("");
      setDisableCode("");
      await refreshUser();
    } finally {
      setLoading(false);
    }
  }

  const setupOnly = !mfaEnabled;
  const { isMobile } = useViewport();

  return (
    <>
      {isMobile && !setupOnly && <MobileTopBar title="Security" backHref="/settings" />}
      <div
        className={cn(
          "mx-auto w-full max-w-2xl p-3 sm:p-6 space-y-6",
          setupOnly && "flex min-h-screen flex-col justify-center",
        )}
      >
        <div className={cn(setupOnly && "text-center", !setupOnly && "hidden sm:block")}>
          <h1 className="text-2xl font-semibold tracking-tight">
            {setupOnly ? "Secure your account" : "Security"}
          </h1>
        {setupOnly && (
          <p className="mt-2 text-sm text-muted-foreground">
            Two-factor authentication is required before you can use the app.
          </p>
        )}
      </div>

      <Card>
        <CardHeader>
          <h2 className="text-lg font-semibold">
            Two-factor authentication
          </h2>
          <p className="text-sm text-muted-foreground">
            Adds a second step to every login using a TOTP authenticator app
            (Google Authenticator, 1Password, Authy, etc.).
          </p>
        </CardHeader>

        <CardBody className="space-y-4">
          {mfaEnabled && state.kind !== "enabled" ? (
            <>
              <p className="text-sm">
                Status: <strong className="text-emerald-600">Enabled</strong>
              </p>
              <div className="space-y-2 rounded-md border border-border p-4">
                <p className="text-sm font-medium">Disable MFA</p>
                <p className="text-xs text-muted-foreground">
                  Requires your password plus a current code (or backup code).
                </p>
                <Input
                  type="password"
                  placeholder="Password"
                  value={disablePassword}
                  onChange={(e) => setDisablePassword(e.target.value)}
                />
                <Input
                  type="text"
                  inputMode="numeric"
                  placeholder="6-digit code or backup code"
                  value={disableCode}
                  onChange={(e) => setDisableCode(e.target.value)}
                />
                <Button
                  variant="destructive"
                  onClick={disableMfa}
                  isLoading={loading}
                  disabled={!disablePassword || !disableCode}
                >
                  Disable MFA
                </Button>
              </div>
            </>
          ) : state.kind === "idle" ? (
            <>
              <p className="text-sm">
                Status: <strong className="text-muted-foreground">Not enabled</strong>
              </p>
              <Button onClick={startSetup} isLoading={loading}>
                Set up 2FA
              </Button>
            </>
          ) : state.kind === "pending" ? (
            <div className="space-y-4">
              <p className="text-sm">
                Scan the QR code with your authenticator app, then enter the
                6-digit code below to confirm.
              </p>
              <div className="flex justify-center rounded-md bg-white p-4 border border-border">
                <QRCodeSVG value={state.otpauthUri} size={192} level="M" />
              </div>
              <div className="rounded-md bg-muted/50 p-3 space-y-2">
                <p className="text-xs font-medium text-muted-foreground">
                  Can&apos;t scan? Enter this secret manually:
                </p>
                <code className="block text-sm font-mono break-all">
                  {state.secret}
                </code>
                <p className="text-xs font-medium text-muted-foreground mt-2">
                  On mobile, tap to open directly in your authenticator app:
                </p>
                <a
                  href={state.otpauthUri}
                  className="block text-xs text-primary break-all underline"
                >
                  {state.otpauthUri}
                </a>
              </div>
              <div className="space-y-2">
                <Input
                  type="text"
                  inputMode="numeric"
                  placeholder="6-digit code"
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                />
                <Button onClick={verifyCode} isLoading={loading} disabled={code.length !== 6}>
                  Verify & enable
                </Button>
              </div>
            </div>
          ) : state.kind === "enabled" ? (
            <div className="space-y-3">
              <p className="text-sm font-medium text-emerald-600">
                MFA is now enabled.
              </p>
              <p className="text-sm">
                Save these backup codes in a password manager. Each code works
                exactly once. They are your only way back in if you lose your
                authenticator device — we cannot regenerate them later.
              </p>
              <pre className="rounded-md bg-muted/50 p-3 text-sm font-mono">
                {state.backupCodes.join("\n")}
              </pre>
              <Button
                variant="secondary"
                onClick={() => {
                  navigator.clipboard.writeText(state.backupCodes.join("\n")).catch(() => {});
                }}
              >
                Copy to clipboard
              </Button>
              <div className="flex justify-end pt-2">
                <Button
                  onClick={finishRequiredSetup}
                  isLoading={loading || completeOnboarding.isPending}
                >
                  {setupOnly ? "I saved these codes, start walkthrough" : "I saved these codes"}
                </Button>
              </div>
            </div>
          ) : (
            <p className="text-sm text-destructive">{state.message}</p>
          )}

          {message && <p className="text-sm text-muted-foreground">{message}</p>}
        </CardBody>
      </Card>
      </div>
    </>
  );
}
