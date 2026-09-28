import { useLicence } from "../hooks/useLicence";
import { licenceStateSentence, type LicenceStatus } from "../lib/licence";

export const VIEW_ONLY_EXPLANATION =
  "Nexus is view-only: existing documentation, scans and run history remain readable, but new scans, documentation generations and chat turns are disabled until a valid licence is installed.";

function expiryDate(status: LicenceStatus): string | null {
  if (!status.period_end) return null;
  const date = new Date(status.period_end);
  return Number.isNaN(date.getTime()) ? null : date.toISOString().slice(0, 10);
}

function daysPhrase(days: number): string {
  if (days <= 0) return "today";
  return days === 1 ? "in 1 day" : `in ${days} days`;
}

/**
 * Presentation only. Renders nothing when the status is unknown, enforcement
 * is off, or the licence is valid and not near its end.
 */
export function LicenceBannerView({ status }: { status: LicenceStatus | null }) {
  if (!status || status.enforcement !== "enforce") return null;

  if (status.state !== "valid") {
    return (
      <div
        role="alert"
        data-testid="licence-banner"
        data-licence-state={status.state}
        className="border-b border-amber-400/30 bg-amber-400/5"
      >
        <p className="max-w-7xl mx-auto px-6 py-2.5 text-sm text-amber-400">
          <span className="font-medium">{licenceStateSentence(status)}</span> {VIEW_ONLY_EXPLANATION}
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
      className="border-b border-amber-400/20 bg-amber-400/[0.03]"
    >
      <p className="max-w-7xl mx-auto px-6 py-2 text-sm text-amber-400">
        The licence for this instance expires {when}
        {end && typeof days === "number" ? ` (${end})` : ""}. After that Nexus becomes view-only until
        it is renewed.
      </p>
    </div>
  );
}

/** Mounted once in `App.tsx`, between the sticky header and `<main>`. */
export default function LicenceBanner() {
  const { status } = useLicence();
  return <LicenceBannerView status={status} />;
}
