import { z } from "zod";

/**
 * Subscription licence: the browser's half of the contract.
 *
 * The backend refuses a mutating call on an expired, missing or exhausted
 * licence with HTTP 402 and a structured `detail`. That refusal is NOT an auth
 * failure: the session is fine, the instance is simply view-only. Everything
 * here exists so a 402 is typed as such and can never reach the paths that
 * treat a status code as "logged out" (`lib/session.ts classifyAuthFailure`,
 * `hooks/useAuth.ts`), which only a real 401 may enter.
 *
 * Leaf module on purpose: `lib/authedFetch.ts`, `lib/mcpClient.ts` and
 * `hooks/useStreamMessage.ts` all import it, and it imports none of them.
 */

export const LICENCE_REFUSAL_CODES = [
  "licence_expired",
  "licence_not_yet_valid",
  "licence_missing",
  "licence_invalid",
  "feature_not_in_plan",
  "quota_exhausted",
] as const;

export type LicenceRefusalCode = (typeof LICENCE_REFUSAL_CODES)[number];

export type LicenceCapability = "chat" | "generation" | "scan";

export const LicenceRefusalDetailSchema = z.object({
  code: z.enum(LICENCE_REFUSAL_CODES),
  message: z.string(),
  capability: z.enum(["chat", "generation", "scan"]).nullable().optional(),
  used: z.number().nullable().optional(),
  limit: z.number().nullable().optional(),
  period_end: z.string().nullable().optional(),
  plan: z.string().nullable().optional(),
});

export type LicenceRefusalDetail = z.infer<typeof LicenceRefusalDetailSchema>;

export const LicenceStateSchema = z.enum([
  "valid",
  "expired",
  "not_yet_valid",
  "missing",
  "invalid",
]);

export type LicenceState = z.infer<typeof LicenceStateSchema>;

export const LicenceStatusSchema = z.object({
  state: LicenceStateSchema,
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
  /** Only the MCP server's status carries usage; the RAG backend's does not. */
  usage: z
    .object({
      generations_used: z.number().nullable().optional(),
      generations_limit: z.number().nullable().optional(),
    })
    .nullable()
    .optional(),
});

export type LicenceStatus = z.infer<typeof LicenceStatusSchema>;

/**
 * A request the licence refused. Status 402, never retryable.
 *
 * Deliberately not a subclass of `SessionExpiredError` or of anything
 * `isAuthError` recognises: a caller that branches on auth must fall straight
 * past it.
 */
export class LicenceRefusal extends Error {
  readonly status = 402;
  readonly retryable = false;
  readonly code: LicenceRefusalCode;
  readonly capability: LicenceCapability | null;

  constructor(readonly detail: LicenceRefusalDetail) {
    super(detail.message);
    this.name = "LicenceRefusal";
    this.code = detail.code;
    this.capability = detail.capability ?? null;
  }
}

export function isLicenceRefusal(error: unknown): error is LicenceRefusal {
  return error instanceof LicenceRefusal;
}

function tryParseJson(text: string): unknown {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return undefined;
  }
}

/**
 * Find the refusal detail in any body shape a 402 can arrive in.
 *
 * - raw FastAPI: `{ detail: { code, message, ... } }` (routes that pipe the
 *   upstream body verbatim, e.g. `/api/source/generate`);
 * - the proxy layer (`lib/backendProxy.ts`): `{ error, code, detail: {...} }`;
 * - the detail object on its own;
 * - a legacy proxy that wrapped the raw text: `{ error: "<json text>" }`.
 */
export function parseLicenceRefusalDetail(body: unknown): LicenceRefusalDetail | null {
  if (typeof body === "string") {
    const parsed = tryParseJson(body);
    return parsed === undefined ? null : parseLicenceRefusalDetail(parsed);
  }
  if (!body || typeof body !== "object") return null;

  const direct = LicenceRefusalDetailSchema.safeParse(body);
  if (direct.success) return direct.data;

  const record = body as Record<string, unknown>;
  for (const candidate of [record.detail, record.error]) {
    if (candidate === undefined || candidate === null) continue;
    const nested = parseLicenceRefusalDetail(candidate);
    if (nested) return nested;
  }
  return null;
}

/**
 * The typed refusal for a 402 body, or null when the body is not one.
 *
 * A 402 whose body does not match the contract still yields a refusal with a
 * generic message: the status alone is the verdict "not allowed by the
 * licence", and falling back to a generic error would present it as a fault.
 */
export function licenceRefusalFromBody(status: number, body: unknown): LicenceRefusal | null {
  if (status !== 402) return null;
  const detail = parseLicenceRefusalDetail(body) ?? {
    code: "licence_invalid" as const,
    message: "This action is not available under the current licence.",
  };
  return new LicenceRefusal(detail);
}

/**
 * Read a 402 response into a `LicenceRefusal` and announce it, or return null.
 *
 * Consumes the body only when the status is 402. Announcing here is what makes
 * "a 402 anywhere refreshes the banner" hold for callers that use bare `fetch`.
 */
export async function licenceRefusalFromResponse(res: Response): Promise<LicenceRefusal | null> {
  if (res.status !== 402) return null;
  const text = await res.text().catch(() => "");
  const refusal = licenceRefusalFromBody(402, text ? tryParseJson(text) ?? text : null);
  notifyLicenceRefused();
  return refusal;
}

// ---------------------------------------------------------------------------
// Invalidation: any 402, anywhere, re-reads the licence status.
// ---------------------------------------------------------------------------

type LicenceRefusedListener = () => void;

const refusedListeners = new Set<LicenceRefusedListener>();

/** Collapses the burst of 402s one expired licence produces into one re-read. */
const REFUSAL_NOTIFY_THROTTLE_MS = 1_000;
let lastRefusalNotifiedAt = 0;

export function onLicenceRefused(listener: LicenceRefusedListener): () => void {
  refusedListeners.add(listener);
  return () => {
    refusedListeners.delete(listener);
  };
}

export function notifyLicenceRefused(): void {
  const now = Date.now();
  if (now - lastRefusalNotifiedAt < REFUSAL_NOTIFY_THROTTLE_MS) return;
  lastRefusalNotifiedAt = now;
  for (const listener of refusedListeners) listener();
}

/** Test seam, same purpose as `resetSessionRevalidationThrottle`. */
export function resetLicenceRefusedThrottle(): void {
  lastRefusalNotifiedAt = 0;
}

// ---------------------------------------------------------------------------
// Decisions the UI makes from a status.
// ---------------------------------------------------------------------------

const CAPABILITY_LABEL: Record<LicenceCapability, string> = {
  chat: "chat",
  generation: "documentation generation",
  scan: "scanning",
};

function formatDate(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return date.toISOString().slice(0, 10);
}

/** One sentence naming why the licence is not in force. */
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

/** Generations used and allowed this period, when the status carries both. */
export function generationUsage(
  status: LicenceStatus | null | undefined,
): { used: number; limit: number } | null {
  const used = status?.usage?.generations_used;
  const limit = status?.usage?.generations_limit ?? status?.limits.generations_per_period;
  if (typeof used !== "number" || typeof limit !== "number") return null;
  return { used, limit };
}

/**
 * Why `capability` is refused under `status`, or null when it is allowed.
 *
 * Unknown status (not loaded yet, or the status call failed) and enforcement
 * `off` never block: the backend is the enforcer, and a UI that locks itself on
 * a failed status read would turn a transient blip into an outage. Scans are
 * not a plan feature, so they are gated on licence state only.
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
