"use client";

import { AlertTriangle, Clock } from "lucide-react";
import { useLicence } from "@/hooks/useLicence";
import {
  licenceStateSentence,
  type LicenceRefusalDetail,
  type LicenceStatus,
} from "@/lib/licence";

/** Shown whenever the instance is view-only, so no screen has to explain it again. */
export const VIEW_ONLY_EXPLANATION =
  "Nexus is view-only: existing documentation and conversations remain readable, but new chat turns, scans and documentation generations are disabled until a valid licence is installed.";

function expiryDate(status: LicenceStatus): string | null {
  if (!status.period_end) return null;
  const date = new Date(status.period_end);
  return Number.isNaN(date.getTime()) ? null : date.toISOString().slice(0, 10);
}

function daysPhrase(days: number): string {
  if (days <= 0) return "today";
  return days === 1 ? "in 1 day" : `in ${days} days`;
}

interface LicenceBannerViewProps {
  status: LicenceStatus | null;
}

/**
 * Presentation only; `LicenceBanner` feeds it from the shared status.
 *
 * Renders nothing when the status is unknown, enforcement is off, or the
 * licence is valid and not close to its end.
 */
export function LicenceBannerView({ status }: LicenceBannerViewProps) {
  if (!status || status.enforcement !== "enforce") return null;

  if (status.state !== "valid") {
    return (
      <div
        role="alert"
        data-testid="licence-banner"
        data-licence-state={status.state}
        className="flex items-start gap-2 border-b border-amber-300 bg-amber-50 px-4 py-2.5 text-sm text-amber-900"
      >
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
        <p>
          <span className="font-medium">{licenceStateSentence(status)}</span>{" "}
          {VIEW_ONLY_EXPLANATION}
        </p>
      </div>
    );
  }

  if (!status.near_expiry) return null;

  const end = expiryDate(status);
  const days = status.days_remaining;
  const when = typeof days === "number" ? daysPhrase(days) : end ? `on ${end}` : "soon";
  return (
    <div
      role="status"
      data-testid="licence-banner"
      data-licence-state="near_expiry"
      className="flex items-start gap-2 border-b border-amber-200 bg-amber-50/70 px-4 py-2 text-sm text-amber-900"
    >
      <Clock className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
      <p>
        The licence for this instance expires {when}
        {end && typeof days === "number" ? ` (${end})` : ""}. After that Nexus becomes view-only
        until it is renewed.
      </p>
    </div>
  );
}

/** The global banner, mounted once in `components/Layout.tsx`. */
export function LicenceBanner() {
  const { status } = useLicence();
  return <LicenceBannerView status={status} />;
}

/**
 * A turn the licence refused, rendered where the answer would have been.
 *
 * Not an error bubble and not retryable: the refusal is a fact about the
 * instance, and a retry button would promise an outcome the server will refuse.
 */
export function LicenceRefusalNotice({ refusal }: { refusal: LicenceRefusalDetail }) {
  return (
    <div
      role="alert"
      data-testid="licence-refusal"
      data-refusal-code={refusal.code}
      className="flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2.5 text-sm text-amber-900"
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
      <p>{refusal.message}</p>
    </div>
  );
}
