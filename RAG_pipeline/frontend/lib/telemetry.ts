/**
 * Lightweight wizard telemetry. Fire-and-forget; errors are swallowed so the
 * UI never blocks on telemetry. The event stream is used by admins to see
 * which platforms/tools users actually pick, so we know what to build next.
 */

export type WizardEventType =
  | "wizard_step_viewed"
  | "wizard_platform_selected"
  | "wizard_platform_deselected"
  | "wizard_tool_selected"
  | "wizard_tool_deselected"
  | "wizard_source_method"
  | "wizard_completed"
  | "tour_started"
  | "tour_skipped";

export interface WizardEventPayload {
  [key: string]: string | number | boolean | null | undefined;
}

export function trackWizardEvent(
  type: WizardEventType,
  step?: number | null,
  payload?: WizardEventPayload,
): void {
  // Only run in the browser — guards against SSR / test environments
  if (typeof window === "undefined") return;

  const body = JSON.stringify({
    event_type: type,
    step: step ?? null,
    payload: payload ?? {},
  });

  try {
    // Prefer sendBeacon for the "tour_skipped" / unload-adjacent cases;
    // fall back to fetch for general events.
    if (
      typeof navigator !== "undefined" &&
      typeof navigator.sendBeacon === "function" &&
      (type === "tour_started" || type === "tour_skipped" || type === "wizard_completed")
    ) {
      const blob = new Blob([body], { type: "application/json" });
      if (navigator.sendBeacon("/api/telemetry/wizard-event", blob)) return;
    }
    void fetch("/api/telemetry/wizard-event", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      keepalive: true,
    }).catch(() => {
      // swallow — telemetry never blocks UX
    });
  } catch {
    // swallow
  }
}
