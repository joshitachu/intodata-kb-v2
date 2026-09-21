"use client";

import { useEffect, useRef, useState } from "react";
import { AlertCircle, Loader2, Save } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  budgetConflictMessage,
  guidanceErrorMessage,
  usePersonPreferences,
  useSavePersonPreferences,
} from "@/hooks/useGuidance";
import {
  AUDIENCE_LABELS,
  AUDIENCE_OPTIONS,
  COMMUNICATION_LABELS,
  COMMUNICATION_OPTIONS,
  DEPTH_LABELS,
  DEPTH_OPTIONS,
  EMPTY_STYLE_PREFERENCE,
  FORMATTING_LABELS,
  FORMATTING_OPTIONS,
  ORGANIZATION_LABELS,
  ORGANIZATION_OPTIONS,
  TONE_LABELS,
  TONE_OPTIONS,
  isEmptyStyle,
  type CommunicationOption,
  type FormattingOption,
  type StylePreference,
} from "@/types/guidance";

interface PreferencesEditorProps {
  projectId: string;
}

const selectClass =
  "w-full appearance-none rounded-md border border-input bg-background px-2 py-1.5 " +
  "text-sm focus:outline-none focus:ring-1 focus:ring-ring";

/**
 * How *this* person wants answers written, for this project.
 *
 * Every control is a closed choice and none of them is a text box. That is not
 * a styling preference, it is the contract: this is the level most exposed to
 * being written back from a conversation, so it is deliberately the level with
 * no room to express an instruction. The server rejects free text here by
 * design, and offering a box it would refuse would be a lie about what this
 * level can do.
 */
export function PreferencesEditor({ projectId }: PreferencesEditorProps) {
  const { data, isLoading, error } = usePersonPreferences(projectId);
  const save = useSavePersonPreferences(projectId);
  const [draft, setDraft] = useState<StylePreference>(EMPTY_STYLE_PREFERENCE);

  const stored = data?.style ?? null;
  const storedRef = useRef(stored);
  storedRef.current = stored;

  // Re-seeded on a *value* change, not on every refetch. React Query hands back
  // a fresh object each time it revalidates (window focus, after a save), and
  // depending on its identity would silently discard selections the user had
  // made but not yet saved.
  const storedKey = JSON.stringify(stored);
  useEffect(() => {
    setDraft(storedRef.current ?? EMPTY_STYLE_PREFERENCE);
  }, [storedKey]);

  function toggle<T extends FormattingOption | CommunicationOption>(
    field: "formatting" | "communication",
    option: T,
  ) {
    setDraft((current) => {
      const list = current[field] as T[];
      const next = list.includes(option)
        ? list.filter((item) => item !== option)
        : [...list, option];
      return { ...current, [field]: next } as StylePreference;
    });
  }

  if (isLoading) {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading your preferences...
      </p>
    );
  }

  if (error) {
    return (
      <p className="text-sm text-destructive" role="alert">
        {guidanceErrorMessage(error, "Could not load your answer preferences")}
      </p>
    );
  }

  const empty = isEmptyStyle(draft);
  const budgetError = budgetConflictMessage(save.error);

  return (
    <div className="space-y-3">
      {stored === null && (
        <p
          data-testid="preferences-empty"
          className="rounded-md border border-dashed border-border bg-muted/30 px-3 py-2 text-xs text-muted-foreground"
        >
          You have not set anything here, so your answers come out the way the
          organization and project settings say. For example, choose{" "}
          <span className="text-foreground">Audience: Executive</span> and{" "}
          <span className="text-foreground">Tone: Concise</span> and you get short
          answers aimed at a client sponsor, while a colleague on the same project
          keeps getting the detailed engineering version.
        </p>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor="pref-audience" className="text-xs font-medium text-foreground">
            Audience
          </label>
          <p className="mb-1 text-[11px] text-muted-foreground">Who the answer is written for.</p>
          <select
            id="pref-audience"
            value={draft.audience ?? ""}
            onChange={(event) =>
              setDraft({
                ...draft,
                audience: event.target.value
                  ? (event.target.value as StylePreference["audience"])
                  : null,
              })
            }
            className={selectClass}
          >
            <option value="">No preference</option>
            {AUDIENCE_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {AUDIENCE_LABELS[option]}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label htmlFor="pref-tone" className="text-xs font-medium text-foreground">
            Tone
          </label>
          <p className="mb-1 text-[11px] text-muted-foreground">
            How much the answer explains itself.
          </p>
          <select
            id="pref-tone"
            value={draft.tone ?? ""}
            onChange={(event) =>
              setDraft({
                ...draft,
                tone: event.target.value
                  ? (event.target.value as StylePreference["tone"])
                  : null,
              })
            }
            className={selectClass}
          >
            <option value="">No preference</option>
            {TONE_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {TONE_LABELS[option]}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label htmlFor="pref-depth" className="text-xs font-medium text-foreground">
            Technical depth
          </label>
          <p className="mb-1 text-[11px] text-muted-foreground">
            How far into the detail an answer goes.
          </p>
          <select
            id="pref-depth"
            value={draft.technical_depth ?? ""}
            onChange={(event) =>
              setDraft({
                ...draft,
                technical_depth: event.target.value
                  ? (event.target.value as StylePreference["technical_depth"])
                  : null,
              })
            }
            className={selectClass}
          >
            <option value="">No preference</option>
            {DEPTH_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {DEPTH_LABELS[option]}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label htmlFor="pref-organization" className="text-xs font-medium text-foreground">
            Answer organization
          </label>
          <p className="mb-1 text-[11px] text-muted-foreground">
            How the answer is laid out on the page.
          </p>
          <select
            id="pref-organization"
            value={draft.output_organization ?? ""}
            onChange={(event) =>
              setDraft({
                ...draft,
                output_organization: event.target.value
                  ? (event.target.value as StylePreference["output_organization"])
                  : null,
              })
            }
            className={selectClass}
          >
            <option value="">No preference</option>
            {ORGANIZATION_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {ORGANIZATION_LABELS[option]}
              </option>
            ))}
          </select>
        </div>
      </div>

      <fieldset>
        <legend className="text-xs font-medium text-foreground">Formatting</legend>
        <p className="text-[11px] text-muted-foreground">
          What you want answers to use when it helps.
        </p>
        <div className="mt-1 flex flex-wrap gap-3">
          {FORMATTING_OPTIONS.map((option) => (
            <label key={option} className="flex items-center gap-1.5 text-sm">
              <input
                type="checkbox"
                checked={draft.formatting.includes(option)}
                onChange={() => toggle("formatting", option)}
                className="h-3.5 w-3.5 rounded border-input"
              />
              {FORMATTING_LABELS[option]}
            </label>
          ))}
        </div>
      </fieldset>

      <fieldset>
        <legend className="text-xs font-medium text-foreground">Communication</legend>
        <p className="text-[11px] text-muted-foreground">
          What you want every answer to do.
        </p>
        <div className="mt-1 flex flex-wrap gap-3">
          {COMMUNICATION_OPTIONS.map((option) => (
            <label key={option} className="flex items-center gap-1.5 text-sm">
              <input
                type="checkbox"
                checked={draft.communication.includes(option)}
                onChange={() => toggle("communication", option)}
                className="h-3.5 w-3.5 rounded border-input"
              />
              {COMMUNICATION_LABELS[option]}
            </label>
          ))}
        </div>
      </fieldset>

      {budgetError ? (
        <p
          role="alert"
          className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200"
        >
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            <strong className="font-medium">No room left here.</strong> {budgetError}
          </span>
        </p>
      ) : save.error ? (
        <p className="text-xs text-destructive" role="alert">
          {guidanceErrorMessage(save.error, "Could not save your answer preferences")}
        </p>
      ) : null}

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => save.mutate(draft)}
          disabled={empty || save.isPending}
          title={
            empty
              ? "Pick at least one preference above before saving."
              : "Save your preferences"
          }
          className={cn(
            "inline-flex h-8 items-center gap-1.5 rounded-md border border-border px-3",
            "text-xs font-medium transition-colors hover:bg-muted",
            "disabled:cursor-not-allowed disabled:opacity-50",
          )}
        >
          {save.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Save className="h-3.5 w-3.5" />
          )}
          Save preferences
        </button>
        {empty && (
          <span className="text-xs text-muted-foreground">
            Pick at least one preference above before saving.
          </span>
        )}
        {save.isSuccess && !save.isPending && (
          <span className="text-xs text-muted-foreground">Saved.</span>
        )}
      </div>
    </div>
  );
}
