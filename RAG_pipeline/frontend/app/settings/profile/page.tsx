"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Shield, CheckCircle2, AlertCircle } from "lucide-react";
import { useAuthContext } from "@/components/AuthProvider";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { MobileTopBar } from "@/components/mobile/MobileTopBar";
import { useViewport } from "@/hooks/useViewport";

type PasswordState =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "success" }
  | { kind: "error"; message: string };

function formatLastLogin(iso: string | null): string {
  if (!iso) return "Never";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(iso));
}

export default function ProfilePage() {
  const { user } = useAuthContext();
  const router = useRouter();

  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [pwState, setPwState] = useState<PasswordState>({ kind: "idle" });

  if (!user) return null;
  const { isMobile } = useViewport();

  const newPasswordTooShort = newPassword.length > 0 && newPassword.length < 12;
  const passwordsMismatch = confirmPassword.length > 0 && newPassword !== confirmPassword;
  const sameAsCurrentPassword = newPassword.length > 0 && newPassword === currentPassword;

  const canSubmit =
    currentPassword.length > 0 &&
    newPassword.length >= 12 &&
    newPassword === confirmPassword &&
    newPassword !== currentPassword &&
    pwState.kind !== "submitting";

  async function handlePasswordChange(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;

    setPwState({ kind: "submitting" });
    try {
      const res = await fetch("/api/auth/change-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setPwState({ kind: "error", message: data.error || "Password change failed." });
        return;
      }
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setPwState({ kind: "success" });
    } catch {
      setPwState({ kind: "error", message: "Password change failed. Please try again." });
    }
  }

  return (
    <>
      {isMobile && <MobileTopBar title="Profile" backHref="/settings" />}
      <div className="max-w-2xl mx-auto px-3 sm:px-4 py-6 sm:py-10 space-y-6">
        <h1 className="hidden sm:block text-2xl font-semibold text-foreground">Profile</h1>

        {/* Profile info */}
        <Card>
          <CardHeader>
            <h2 className="text-sm font-semibold text-foreground">Account</h2>
          </CardHeader>
          <CardBody>
            <dl className="grid grid-cols-1 sm:grid-cols-[140px_1fr] gap-y-3 text-sm">
            <dt className="text-muted-foreground">Username</dt>
            <dd className="text-foreground font-medium">{user.username}</dd>

            <dt className="text-muted-foreground">Email</dt>
            <dd className="text-foreground">{user.email}</dd>

            <dt className="text-muted-foreground">Roles</dt>
            <dd className="text-foreground">
              {user.roles.length > 0 ? user.roles.join(", ") : "—"}
            </dd>

            <dt className="text-muted-foreground">Last login</dt>
            <dd className="text-foreground">{formatLastLogin(user.last_login_at)}</dd>
          </dl>
        </CardBody>
      </Card>

      {/* Change password */}
      <Card>
        <CardHeader>
          <h2 className="text-sm font-semibold text-foreground">Password</h2>
        </CardHeader>
        <CardBody>
          {!user.has_password ? (
            <p className="text-sm text-muted-foreground">
              This account uses{" "}
              <span className="font-medium text-foreground">
                {user.sso_provider ?? "email code"}
              </span>{" "}
              sign-in and has no password set. Contact an administrator if you need password access.
            </p>
          ) : (
            <form onSubmit={handlePasswordChange} className="space-y-4">
              <div className="space-y-1.5">
                <label className="text-sm text-muted-foreground" htmlFor="current-password">
                  Current password
                </label>
                <Input
                  id="current-password"
                  type="password"
                  autoComplete="current-password"
                  value={currentPassword}
                  onChange={(e) => {
                    setCurrentPassword(e.target.value);
                    if (pwState.kind !== "idle") setPwState({ kind: "idle" });
                  }}
                />
              </div>

              <div className="space-y-1.5">
                <label className="text-sm text-muted-foreground" htmlFor="new-password">
                  New password
                  <span className="ml-1 text-xs text-muted-foreground/70">(min. 12 characters)</span>
                </label>
                <Input
                  id="new-password"
                  type="password"
                  autoComplete="new-password"
                  value={newPassword}
                  hasError={newPasswordTooShort || sameAsCurrentPassword}
                  onChange={(e) => {
                    setNewPassword(e.target.value);
                    if (pwState.kind !== "idle") setPwState({ kind: "idle" });
                  }}
                />
                {newPasswordTooShort && (
                  <p className="text-xs text-destructive">Must be at least 12 characters.</p>
                )}
                {sameAsCurrentPassword && (
                  <p className="text-xs text-destructive">Must differ from current password.</p>
                )}
              </div>

              <div className="space-y-1.5">
                <label className="text-sm text-muted-foreground" htmlFor="confirm-password">
                  Confirm new password
                </label>
                <Input
                  id="confirm-password"
                  type="password"
                  autoComplete="new-password"
                  value={confirmPassword}
                  hasError={passwordsMismatch}
                  onChange={(e) => {
                    setConfirmPassword(e.target.value);
                    if (pwState.kind !== "idle") setPwState({ kind: "idle" });
                  }}
                />
                {passwordsMismatch && (
                  <p className="text-xs text-destructive">Passwords do not match.</p>
                )}
              </div>

              {pwState.kind === "success" && (
                <div className="flex items-center gap-2 text-sm text-emerald-600">
                  <CheckCircle2 className="h-4 w-4 shrink-0" />
                  Password updated successfully.
                </div>
              )}
              {pwState.kind === "error" && (
                <div className="flex items-center gap-2 text-sm text-destructive">
                  <AlertCircle className="h-4 w-4 shrink-0" />
                  {pwState.message}
                </div>
              )}

              <Button
                type="submit"
                disabled={!canSubmit}
                isLoading={pwState.kind === "submitting"}
              >
                Update password
              </Button>
            </form>
          )}
        </CardBody>
      </Card>

      {/* 2FA status */}
      <Card>
        <CardHeader>
          <h2 className="text-sm font-semibold text-foreground">Two-factor authentication</h2>
        </CardHeader>
        <CardBody>
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-center gap-3">
              <Shield
                className={`h-5 w-5 shrink-0 ${user.mfa_enabled ? "text-emerald-500" : "text-muted-foreground"}`}
              />
              <div>
                <p className="text-sm font-medium text-foreground">
                  {user.mfa_enabled ? "Enabled" : "Not configured"}
                </p>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {user.mfa_enabled
                    ? "Authenticator app is protecting your account."
                    : "Add an authenticator app for extra security."}
                </p>
              </div>
            </div>
            <Button
              variant={user.mfa_enabled ? "secondary" : "primary"}
              size="sm"
              onClick={() => router.push("/settings/security")}
            >
              {user.mfa_enabled ? "Manage" : "Set up"}
            </Button>
          </div>
        </CardBody>
      </Card>
      </div>
    </>
  );
}
