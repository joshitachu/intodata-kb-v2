"use client";

import { useState } from "react";
import { Ban, Check, CircleHelp, Send, ShieldCheck, X } from "lucide-react";
import type { HarnessPendingAction as HarnessPendingActionValue } from "@/types";

interface HarnessPendingActionProps {
  action: HarnessPendingActionValue;
}

export function HarnessPendingAction({ action }: HarnessPendingActionProps) {
  const [submitting, setSubmitting] = useState(false);
  const [resolved, setResolved] = useState(false);
  const [resolvedMessage, setResolvedMessage] = useState("Response received");
  const [artifactLinks, setArtifactLinks] = useState<Array<{ label: string; url: string }>>([]);
  const [error, setError] = useState<string | null>(null);
  const [textValue, setTextValue] = useState("");

  async function submit(path: "resume" | "cancel", responseValue?: string) {
    setSubmitting(true);
    setError(null);
    try {
      const fetchResponse = await fetch(
        `/api/harness/workflows/${encodeURIComponent(action.workflow_run_id)}/${path}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(
            path === "resume"
              ? {
                  interrupt_id: action.interrupt_id,
                  expected_version: action.expected_version,
                  response: responseValue,
                }
              : { expected_version: action.expected_version },
          ),
        },
      );
      if (!fetchResponse.ok) {
        const data = await fetchResponse.json().catch(() => null) as { error?: unknown } | null;
        throw new Error(typeof data?.error === "string" ? data.error : "Action could not be completed");
      }
      const data = await fetchResponse.json().catch(() => null) as {
        message?: unknown;
        run?: { artifact_links?: unknown };
      } | null;
      if (typeof data?.message === "string") setResolvedMessage(data.message);
      if (Array.isArray(data?.run?.artifact_links)) {
        setArtifactLinks(
          data.run.artifact_links.flatMap((item) => {
            if (!item || typeof item !== "object") return [];
            const link = item as Record<string, unknown>;
            return typeof link.label === "string" && typeof link.url === "string"
              ? [{ label: link.label, url: link.url }]
              : [];
          }),
        );
      }
      setResolved(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Action could not be completed");
    } finally {
      setSubmitting(false);
    }
  }

  if (resolved) {
    return (
      <div className="mb-3 space-y-1.5 text-xs text-muted-foreground" role="status">
        <div className="inline-flex items-center gap-1.5">
          <Check className="h-3.5 w-3.5 text-green-600" />
          {resolvedMessage}
        </div>
        {artifactLinks.length > 0 && (
          <div className="flex flex-wrap gap-x-3 gap-y-1 pl-5">
            {artifactLinks.map((link) => (
              <a key={`${link.label}:${link.url}`} href={link.url} className="underline underline-offset-2 hover:text-foreground">
                {link.label}
              </a>
            ))}
          </div>
        )}
      </div>
    );
  }

  const isApproval = action.kind === "approval";
  const Icon = isApproval ? ShieldCheck : CircleHelp;

  return (
    <div className="mb-3 max-w-xl border-l-2 border-primary/50 pl-3" aria-label="Workflow action required">
      <div className="flex items-start gap-2">
        <Icon className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1 space-y-2.5">
          <p className="text-sm text-foreground">{action.prompt}</p>
          <div className="flex flex-wrap gap-2">
            {isApproval ? (
              <>
                <button type="button" disabled={submitting} onClick={() => void submit("resume", "approve")} className="inline-flex min-h-9 items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
                  <Check className="h-3.5 w-3.5" /> Approve
                </button>
                <button type="button" disabled={submitting} onClick={() => void submit("resume", "decline")} className="inline-flex min-h-9 items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs font-medium hover:bg-muted disabled:opacity-50">
                  <X className="h-3.5 w-3.5" /> Decline
                </button>
              </>
            ) : (
              action.choices.length > 0 ? (
                action.choices.map((choice) => (
                  <button key={`${choice.label}:${choice.value}`} type="button" disabled={submitting} onClick={() => void submit("resume", choice.value)} className="min-h-9 rounded-md border px-3 py-1.5 text-xs font-medium hover:bg-muted disabled:opacity-50">
                    {choice.label}
                  </button>
                ))
              ) : (
                <form
                  className="flex min-w-0 flex-1 gap-2"
                  onSubmit={(event) => {
                    event.preventDefault();
                    if (textValue.trim()) void submit("resume", textValue.trim());
                  }}
                >
                  <input
                    aria-label="Workflow response"
                    value={textValue}
                    onChange={(event) => setTextValue(event.target.value)}
                    disabled={submitting}
                    className="h-9 min-w-0 flex-1 rounded-md border bg-background px-2.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  />
                  <button type="submit" title="Continue" aria-label="Continue workflow" disabled={submitting || !textValue.trim()} className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
                    <Send className="h-3.5 w-3.5" />
                  </button>
                </form>
              )
            )}
            <button type="button" disabled={submitting} onClick={() => void submit("cancel")} className="inline-flex min-h-9 items-center gap-1.5 rounded-md px-2 py-1.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50">
              <Ban className="h-3.5 w-3.5" /> Cancel
            </button>
          </div>
          {error && <p className="text-xs text-destructive" role="alert">{error}</p>}
        </div>
      </div>
    </div>
  );
}
