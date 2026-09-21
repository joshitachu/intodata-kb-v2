"use client";

import type { GuidanceKind } from "@/types/guidance";

/**
 * What each kind of instruction is, phrased as the question it answers.
 *
 * `KIND_LABELS` in the contract file names the mechanism -- "Output rule",
 * "Evidence weight" -- which is the vocabulary of whoever implemented the
 * renderer, not of the consultant deciding what to write. These are the same
 * four things said as an outcome, and they live in one file so the picker that
 * creates a rule and the row that shows it back can never disagree about what
 * the thing is called.
 *
 * The hints are the teaching copy the level cards used to carry. They followed
 * the editor into the file pane rather than being dropped: a picker option
 * named "What counts as proof" still needs one concrete instance before a
 * reader knows whether their thought belongs there.
 */

/** The kinds a person can create. `style` is written by the preferences form. */
export const EDITABLE_KINDS = [
  "rule",
  "output_rule",
  "vocabulary",
  "evidence_rule",
] as const;
export type EditableKind = (typeof EDITABLE_KINDS)[number];

/** What the org rules file can hold. */
export const ORG_KINDS: readonly EditableKind[] = ["rule", "output_rule"];

/** What the project rules file can hold. */
export const PROJECT_KINDS: readonly EditableKind[] = [
  "rule",
  "vocabulary",
  "evidence_rule",
  "output_rule",
];

/** The picker options, in the wording the sections used to be titled with. */
export const KIND_CHOICE_LABELS: Record<EditableKind, string> = {
  rule: "An instruction it must follow",
  output_rule: "How answers should be written",
  vocabulary: "What a word means here",
  evidence_rule: "What counts as proof",
};

/** The same four things as a badge, short enough to sit on a row. */
export const KIND_ROW_LABELS: Record<GuidanceKind, string> = {
  rule: "Instruction",
  output_rule: "How to write answers",
  vocabulary: "Word meaning",
  evidence_rule: "What counts as proof",
  style: "Answer preferences",
};

/** The visible label of the free-text field, per kind. */
export const KIND_FIELD_LABELS: Record<"rule" | "output_rule", string> = {
  rule: "The instruction",
  output_rule: "The writing rule",
};

export const KIND_FIELD_PLACEHOLDERS: Record<"rule" | "output_rule", string> = {
  rule: "What must it always do?",
  output_rule: "How should an answer be written?",
};

/** What belongs in each kind at the organization level, with one instance. */
export const ORG_KIND_HINTS: Partial<Record<EditableKind, string>> = {
  rule:
    'Standing instructions that hold for every client and every project. For example: "Answer in the language the question was asked in."',
  output_rule:
    'House style for the shape of an answer, whatever it is about. For example: "Lead with the answer, then the evidence behind it."',
};

/** The same, for one project. The instances are in the reader's own domain. */
export const PROJECT_KIND_HINTS: Partial<Record<EditableKind, string>> = {
  rule:
    'Facts about this landscape that should shape every answer about it. For example: "The 4PS interface is read-only - never suggest writing to it."',
  output_rule:
    'Shape rules that apply only to answers about this project. For example: "Always name the job and the environment an answer refers to."',
  vocabulary:
    'Terms this client uses in their own way, so the assistant stops guessing at them. For example: "Koppelvlak" means one integration point between two systems, in one direction.',
  evidence_rule:
    "Which kind of evidence you trust when two sources disagree. For example: a runtime log is strong evidence that a job ran; a naming convention on its own proves nothing.",
};
