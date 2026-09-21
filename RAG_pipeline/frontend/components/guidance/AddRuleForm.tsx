"use client";

import { useState, type FormEvent } from "react";
import { Loader2, Plus } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  EvidenceRulePayloadSchema,
  MAX_RULE_CONTENT_CHARS,
  REFERENCE_ID_PATTERN,
  VocabularyPayloadSchema,
  type GuidanceRuleCreateRequest,
} from "@/types/guidance";
import {
  KIND_CHOICE_LABELS,
  KIND_FIELD_LABELS,
  KIND_FIELD_PLACEHOLDERS,
  type EditableKind,
} from "./kindLabels";

interface AddRuleFormProps {
  /** The kinds this file can hold. The first one is selected to begin with. */
  kinds: readonly EditableKind[];
  /** Stable prefix for input ids, so labels stay bound across a remount. */
  idPrefix: string;
  /** What belongs in each kind, shown under the picker. */
  hints: Partial<Record<EditableKind, string>>;
  /** Non-null disables the whole form and is shown as the reason. */
  disabledReason: string | null;
  isPending: boolean;
  /** Resolves true when the write landed, which is when the form clears. */
  onSubmit: (draft: GuidanceRuleCreateRequest) => Promise<boolean>;
}

const inputClass =
  "w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm " +
  "placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring " +
  "disabled:cursor-not-allowed disabled:opacity-60";

const selectClass =
  "w-full cursor-pointer appearance-none rounded-md border border-input bg-background " +
  "px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-ring " +
  "disabled:cursor-not-allowed disabled:opacity-60";

/**
 * One box for everything a file can hold, with the kind as its first question.
 *
 * There used to be one of these per kind, stacked: four boxes under the project
 * level, two under the organization level, each with its own heading and its own
 * empty state. That is the same decision asked four times, and it is what made
 * the panel read as a wall of forms rather than as a place to write one
 * sentence. Now the kind is a choice inside a single form, and the fields below
 * it change to whatever that choice needs -- free text for an instruction or a
 * writing rule, a term and its meaning for a word, the evidence fields for
 * proof.
 *
 * The kinds are named after what they do, not after the column they are stored
 * in. Those strings live in `kindLabels` so the picker and the rows it creates
 * cannot drift apart.
 *
 * WHEN A COMPLAINT IS ALLOWED TO APPEAR
 *
 * Never before the user has done anything. An empty field someone has not
 * touched is not a mistake, and the panel previously opened with a red "Type
 * the rule first." over a blank box -- which reads as a system already in a bad
 * state before anyone has spoken to it.
 *
 * So a message appears in exactly two situations: the user pressed Add, or the
 * user typed something, left the field, and what they left behind cannot be
 * saved. Any keystroke clears it again, because a person mid-correction does
 * not need to be told about the thing they are already fixing. Switching kind
 * clears it too: a complaint about a field that is no longer on screen is
 * noise.
 *
 * Client-side checks mirror the contract (`VocabularyTerm`,
 * `EvidenceRankingRule`, the 500-character rule limit) purely so a user is told
 * before the round trip. The backend remains the authority and its refusals are
 * surfaced verbatim beside this form.
 */
export function AddRuleForm({
  kinds,
  idPrefix,
  hints,
  disabledReason,
  isPending,
  onSubmit,
}: AddRuleFormProps) {
  const [kind, setKind] = useState<EditableKind>(kinds[0] ?? "rule");
  const [content, setContent] = useState("");
  const [term, setTerm] = useState("");
  const [aliases, setAliases] = useState("");
  const [description, setDescription] = useState("");
  const [evidenceType, setEvidenceType] = useState("");
  const [sourceKind, setSourceKind] = useState("");
  const [weight, setWeight] = useState("50");
  const [localError, setLocalError] = useState<string | null>(null);

  const disabled = disabledReason !== null;
  const isText = kind === "rule" || kind === "output_rule";

  /** True when the user has entered nothing at all for the current kind. */
  function isBlank(): boolean {
    if (isText) return content.trim() === "";
    if (kind === "vocabulary") {
      return term.trim() === "" && aliases.trim() === "" && description.trim() === "";
    }
    return evidenceType.trim() === "" && sourceKind.trim() === "";
  }

  function buildDraft(): GuidanceRuleCreateRequest | string {
    if (kind === "rule" || kind === "output_rule") {
      const text = content.trim();
      if (!text) return "Write the rule before adding it.";
      if (text.length > MAX_RULE_CONTENT_CHARS) {
        return `A rule is limited to ${MAX_RULE_CONTENT_CHARS} characters.`;
      }
      return { kind, content: text };
    }

    if (kind === "vocabulary") {
      const parsed = VocabularyPayloadSchema.safeParse({
        term: term.trim(),
        aliases: aliases
          .split(",")
          .map((alias) => alias.trim())
          .filter((alias) => alias.length > 0),
        description: description.trim(),
      });
      if (!parsed.success) {
        return "A term needs both a name and a description of what it means here.";
      }
      return { kind, payload: parsed.data };
    }

    const weightValue = Number(weight);
    if (!Number.isInteger(weightValue)) return "Weight must be a whole number.";
    const evidence = evidenceType.trim();
    const source = sourceKind.trim();
    if (!REFERENCE_ID_PATTERN.test(evidence)) {
      return "Name the kind of evidence in lowercase, for example runtime_log.";
    }
    if (source && !REFERENCE_ID_PATTERN.test(source)) {
      return "Name the source in lowercase, for example tmc.";
    }
    const parsed = EvidenceRulePayloadSchema.safeParse({
      evidence_type: evidence,
      weight: weightValue,
      source_kind: source || null,
    });
    if (!parsed.success) return "Weight must be between -100 and 100.";
    return { kind, payload: parsed.data };
  }

  /**
   * Any edit clears the complaint.
   *
   * Wrapped around every setter rather than left to each field so a form can
   * never grow a field that keeps a stale error on screen while the user is
   * already correcting it.
   */
  function edit<T>(setter: (value: T) => void): (value: T) => void {
    return (value: T) => {
      setter(value);
      setLocalError(null);
    };
  }

  /**
   * Checked on leaving a field, but only once something has been entered.
   *
   * Tabbing past an untouched box is not an error and must not look like one.
   */
  function handleBlur() {
    if (disabled || isBlank()) {
      setLocalError(null);
      return;
    }
    const draft = buildDraft();
    if (typeof draft === "string") setLocalError(draft);
  }

  function clear() {
    setContent("");
    setTerm("");
    setAliases("");
    setDescription("");
    setEvidenceType("");
    setSourceKind("");
    setWeight("50");
    setLocalError(null);
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (disabled || isPending) return;

    const draft = buildDraft();
    if (typeof draft === "string") {
      setLocalError(draft);
      return;
    }
    setLocalError(null);
    if (await onSubmit(draft)) clear();
  }

  const hint = hints[kind];

  return (
    <form onSubmit={handleSubmit} className="space-y-2" data-testid="add-rule-form">
      <div>
        <label
          htmlFor={`${idPrefix}-kind`}
          className="text-xs font-medium text-foreground"
        >
          What are you adding
        </label>
        <select
          id={`${idPrefix}-kind`}
          value={kind}
          onChange={(event) => {
            setKind(event.target.value as EditableKind);
            setLocalError(null);
          }}
          disabled={disabled}
          title={disabledReason ?? undefined}
          className={cn(selectClass, "mt-1")}
        >
          {kinds.map((option) => (
            <option key={option} value={option}>
              {KIND_CHOICE_LABELS[option]}
            </option>
          ))}
        </select>
        {hint && <p className="mt-1 text-[11px] text-muted-foreground">{hint}</p>}
      </div>

      {isText ? (
        <div>
          <label
            htmlFor={`${idPrefix}-content`}
            className="text-xs text-muted-foreground"
          >
            {KIND_FIELD_LABELS[kind]}
          </label>
          <textarea
            id={`${idPrefix}-content`}
            value={content}
            onChange={(event) => edit(setContent)(event.target.value)}
            onBlur={handleBlur}
            placeholder={KIND_FIELD_PLACEHOLDERS[kind]}
            rows={2}
            maxLength={MAX_RULE_CONTENT_CHARS}
            disabled={disabled}
            title={disabledReason ?? undefined}
            className={cn(inputClass, "resize-y")}
          />
          {content.length > 0 && (
            <p className="mt-0.5 text-[11px] text-muted-foreground">
              {content.length} of {MAX_RULE_CONTENT_CHARS} characters
            </p>
          )}
        </div>
      ) : kind === "vocabulary" ? (
        <div className="grid gap-2 sm:grid-cols-2">
          <div>
            <label htmlFor={`${idPrefix}-term`} className="text-xs text-muted-foreground">
              Term
            </label>
            <input
              id={`${idPrefix}-term`}
              value={term}
              onChange={(event) => edit(setTerm)(event.target.value)}
              onBlur={handleBlur}
              placeholder="Koppelvlak"
              disabled={disabled}
              title={disabledReason ?? undefined}
              className={inputClass}
            />
          </div>
          <div>
            <label htmlFor={`${idPrefix}-aliases`} className="text-xs text-muted-foreground">
              Also called (comma separated)
            </label>
            <input
              id={`${idPrefix}-aliases`}
              value={aliases}
              onChange={(event) => edit(setAliases)(event.target.value)}
              onBlur={handleBlur}
              placeholder="interface, exchange"
              disabled={disabled}
              title={disabledReason ?? undefined}
              className={inputClass}
            />
          </div>
          <div className="sm:col-span-2">
            <label
              htmlFor={`${idPrefix}-description`}
              className="text-xs text-muted-foreground"
            >
              What it means here
            </label>
            <textarea
              id={`${idPrefix}-description`}
              value={description}
              onChange={(event) => edit(setDescription)(event.target.value)}
              onBlur={handleBlur}
              placeholder="One integration point between two systems, in one direction."
              rows={2}
              disabled={disabled}
              title={disabledReason ?? undefined}
              className={cn(inputClass, "resize-y")}
            />
          </div>
        </div>
      ) : (
        <div className="grid gap-2 sm:grid-cols-3">
          <div>
            <label
              htmlFor={`${idPrefix}-evidence-type`}
              className="text-xs text-muted-foreground"
            >
              Kind of evidence
            </label>
            <input
              id={`${idPrefix}-evidence-type`}
              value={evidenceType}
              onChange={(event) => edit(setEvidenceType)(event.target.value)}
              onBlur={handleBlur}
              placeholder="runtime_log"
              disabled={disabled}
              title={disabledReason ?? undefined}
              className={inputClass}
            />
          </div>
          <div>
            <label htmlFor={`${idPrefix}-source`} className="text-xs text-muted-foreground">
              Where it comes from (optional)
            </label>
            <input
              id={`${idPrefix}-source`}
              value={sourceKind}
              onChange={(event) => edit(setSourceKind)(event.target.value)}
              onBlur={handleBlur}
              placeholder="tmc"
              disabled={disabled}
              title={disabledReason ?? undefined}
              className={inputClass}
            />
          </div>
          <div>
            <label htmlFor={`${idPrefix}-weight`} className="text-xs text-muted-foreground">
              How much it counts
            </label>
            <input
              id={`${idPrefix}-weight`}
              type="number"
              min={-100}
              max={100}
              step={1}
              value={weight}
              onChange={(event) => edit(setWeight)(event.target.value)}
              onBlur={handleBlur}
              disabled={disabled}
              title={disabledReason ?? undefined}
              className={inputClass}
            />
            <p className="mt-0.5 text-[11px] text-muted-foreground">
              Above zero: trust it. Below zero: not enough on its own.
            </p>
          </div>
        </div>
      )}

      {localError && (
        <p className="text-xs text-destructive" role="alert">
          {localError}
        </p>
      )}

      <div className="flex items-center gap-2">
        <button
          type="submit"
          disabled={disabled || isPending}
          title={disabledReason ?? undefined}
          className={cn(
            "inline-flex h-8 items-center gap-1.5 rounded-md border border-border px-3",
            "text-xs font-medium transition-colors hover:bg-muted",
            "disabled:cursor-not-allowed disabled:opacity-50",
          )}
        >
          {isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Plus className="h-3.5 w-3.5" />
          )}
          Add
        </button>
        {disabledReason && (
          <span className="text-xs text-muted-foreground">{disabledReason}</span>
        )}
      </div>
    </form>
  );
}
