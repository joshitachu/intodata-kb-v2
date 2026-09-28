import { z } from "zod";
import { LicenceRefusal, type LicenceRefusalDetail } from "./errors";

/**
 * Subscription licence: the dashboard's half of the contract.
 *
 * Mirrors `RAG_pipeline/frontend/lib/licence.ts` (same decisions, same
 * sentences); the two apps are separate builds and are about to be separate
 * repositories, so there is no shared package to hold it. Keep them in step.
 *
 * A 402 from the MCP server means the instance is view-only or the period's
 * quota is used. It is typed as `LicenceRefusal` and must never be treated as
 * a logout -- only a 401 redirects to login.
 */

export { LicenceRefusal };
export type { LicenceRefusalDetail };

export type LicenceCapability = "chat" | "generation" | "scan";

const REFUSAL_CODES = [
  "licence_expired",
  "licence_not_yet_valid",
  "licence_missing",
  "licence_invalid",
  "feature_not_in_plan",
  "quota_exhausted",
] as const;

const LicenceRefusalDetailSchema = z.object({
  code: z.enum(REFUSAL_CODES),
  message: z.string(),
  capability: z.enum(["chat", "generation", "scan"]).nullable().optional(),
  used: z.number().nullable().optional(),
  limit: z.number().nullable().optional(),
  period_end: z.string().nullable().optional(),
  plan: z.string().nullable().optional(),
});

export const LicenceStatusSchema = z.object({
  state: z.enum(["valid", "expired", "not_yet_valid", "missing", "invalid"]),
  reason: z.string().nullable().optional(),
  plan: z.string().nullable().optional(),
  features: z.array(z.string()).default([]),
  limits: z
    .object({ generations_per_period: z.number().nullable().optional() })
    .default({}),
  period_start: z.string().nullable().optional(),
  period_end: z.string().nullable().optional(),
  days_remaining: z.number().nullable().optional(),
  near_expiry: z.boolean().default(false),
  enforcement: z.enum(["enforce", "off"]),
  usage: z
    .object({
      generations_used: z.number().nullable().optional(),
      generations_limit: z.number().nullable().optional(),
    })
    .nullable()
    .optional(),
});

export type LicenceStatus = z.infer<typeof LicenceStatusSchema>;

export function isLicenceRefusal(error: unknown): error is LicenceRefusal {
  return error instanceof LicenceRefusal;
}

/** The refusal detail in `{ detail: {...} }` or the bare detail, or null. */
export function parseLicenceRefusalDetail(body: unknown): LicenceRefusalDetail | null {
  if (!body || typeof body !== "object") return null;
  const direct = LicenceRefusalDetailSchema.safeParse(body);
  if (direct.success) return direct.data;
  const nested = LicenceRefusalDetailSchema.safeParse((body as { detail?: unknown }).detail);
  return nested.success ? nested.data : null;
}

/**
 * The typed refusal for a 402 response body (raw text), or null for any other
 * status. A 402 whose body does not match the contract still yields a refusal:
 * the status alone is the verdict.
 */
export function licenceRefusalFromText(status: number, text: string): LicenceRefusal | null {
  if (status !== 402) return null;
  let body: unknown = null;
  try {
    body = JSON.parse(text);
  } catch {
    // Not JSON; fall through to the generic sentence.
  }
  const detail = parseLicenceRefusalDetail(body) ?? {
    code: "licence_invalid" as const,
    message: "This action is not available under the current licence.",
  };
  return new LicenceRefusal(detail, text);
}

// Any 402, anywhere, re-reads the licence status.

type Listener = () => void;
const listeners = new Set<Listener>();
const NOTIFY_THROTTLE_MS = 1_000;
let lastNotifiedAt = 0;

export function onLicenceRefused(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function notifyLicenceRefused(): void {
  const now = Date.now();
  if (now - lastNotifiedAt < NOTIFY_THROTTLE_MS) return;
  lastNotifiedAt = now;
  for (const listener of listeners) listener();
}

/** Test seam. */
export function resetLicenceRefusedThrottle(): void {
  lastNotifiedAt = 0;
}

const CAPABILITY_LABEL: Record<LicenceCapability, string> = {
  chat: "chat",
  generation: "documentation generation",
  scan: "scanning",
};

function formatDate(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date.toISOString().slice(0, 10);
}

export function licenceStateSentence(status: LicenceStatus): string {
  const end = formatDate(status.period_end);
  const start = formatDate(status.period_start);
  switch (status.state) {
    case "expired":
      return end ? `The licence for this instance expired on ${end}.` : "The licence for this instance has expired.";
    case "not_yet_valid":
      return start
        ? `The licence for this instance is not valid until ${start}.`
        : "The licence for this instance is not valid yet.";
    case "missing":
      return "No licence is installed on this instance.";
    case "invalid":
      return "The licence installed on this instance could not be verified.";
    case "valid":
      return "The licence for this instance is valid.";
  }
}

export function generationUsage(
  status: LicenceStatus | null | undefined,
): { used: number; limit: number } | null {
  const used = status?.usage?.generations_used;
  const limit = status?.usage?.generations_limit ?? status?.limits.generations_per_period;
  if (typeof used !== "number" || typeof limit !== "number") return null;
  return { used, limit };
}

/**
 * Why `capability` is refused, or null when allowed. Unknown status and
 * enforcement `off` never block (the server is the enforcer). Scans are not a
 * plan feature, so they are gated on licence state only.
 */
export function capabilityBlockReason(
  status: LicenceStatus | null | undefined,
  capability: LicenceCapability,
): string | null {
  if (!status || status.enforcement !== "enforce") return null;
  if (status.state !== "valid") {
    return `${licenceStateSentence(status)} Nexus is view-only, so ${CAPABILITY_LABEL[capability]} is disabled.`;
  }
  if (capability !== "scan" && !status.features.includes(capability)) {
    return status.plan
      ? `The "${status.plan}" plan does not include ${CAPABILITY_LABEL[capability]}.`
      : `The current plan does not include ${CAPABILITY_LABEL[capability]}.`;
  }
  if (capability === "generation") {
    const usage = generationUsage(status);
    if (usage && usage.used >= usage.limit) {
      return `All ${usage.limit} documentation generations for this period have been used.`;
    }
  }
  return null;
}
