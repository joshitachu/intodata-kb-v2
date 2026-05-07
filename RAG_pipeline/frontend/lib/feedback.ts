/**
 * Client helper for submitting chat feedback (thumbs up/down).
 * Fire-and-forget — errors are swallowed so the UI never blocks on feedback.
 * Server reconciles duplicates via latest-row-per-(user, message) semantics.
 */

import type { DocumentSource } from "@/types";

export type FeedbackKind = "up" | "down" | "correction" | "cleared";

export interface FeedbackPayload {
  kind: FeedbackKind;
  conversation_id?: number | null;
  message_id?: number | null;
  text?: string | null;
  citations_snapshot?: DocumentSource[] | null;
}

export function submitFeedback(payload: FeedbackPayload): Promise<boolean> {
  if (typeof window === "undefined") return Promise.resolve(false);

  return fetch("/api/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    keepalive: true,
  })
    .then((res) => res.ok || res.status === 204)
    .catch(() => false);
}
