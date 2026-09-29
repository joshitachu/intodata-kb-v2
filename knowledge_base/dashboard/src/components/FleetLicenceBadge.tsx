import type { FleetLicence } from "../api/fleet";

export type LicenceBadgeTone = "ok" | "warn" | "bad" | "muted";

export interface LicenceBadgeInfo {
  label: string;
  tone: LicenceBadgeTone;
}

function days(n: number): string {
  return `${n} day${n === 1 ? "" : "s"}`;
}

/**
 * The fleet-list verdict for one client's licence. `null`/absent means the
 * instance does not report a licence (older build) and reads as Unknown.
 */
export function fleetLicenceBadge(licence: FleetLicence | null | undefined): LicenceBadgeInfo {
  if (!licence) return { label: "Unknown", tone: "muted" };
  const left = typeof licence.days_remaining === "number" ? licence.days_remaining : null;
  switch (licence.state) {
    case "valid":
      if (licence.near_expiry) {
        return { label: left != null ? `Expiring (${days(left)})` : "Expiring", tone: "warn" };
      }
      return { label: left != null ? `Valid (${days(left)})` : "Valid", tone: "ok" };
    case "expired":
      return { label: "Expired", tone: "bad" };
    case "not_yet_valid":
      return { label: "Not yet valid", tone: "warn" };
    case "missing":
      return { label: "Missing", tone: "bad" };
    case "invalid":
      return { label: "Invalid", tone: "bad" };
    case "unknown":
      return { label: "Unknown", tone: "muted" };
  }
}

const TONE_STYLES: Record<LicenceBadgeTone, string> = {
  ok: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  warn: "bg-amber-500/10 text-amber-400 border-amber-500/20",
  bad: "bg-red-500/10 text-red-400 border-red-500/20",
  muted: "bg-white/[0.04] text-slate-500 border-white/[0.1]",
};

export default function FleetLicenceBadge({ licence }: { licence: FleetLicence | null | undefined }) {
  const { label, tone } = fleetLicenceBadge(licence);
  return (
    <span
      data-testid="fleet-licence-badge"
      title={licence ? `Licence: ${label}` : "Licence: unknown (instance not reporting licence)"}
      className={`px-1.5 py-0.5 text-[10px] font-mono rounded border ${TONE_STYLES[tone]}`}
    >
      Licence: {label}
    </span>
  );
}
