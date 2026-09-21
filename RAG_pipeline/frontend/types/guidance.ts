import { z } from "zod";

/**
 * Wire contract for the layered guidance stack (`/api/v1/guidance/*`).
 *
 * Every field the backend publishes is declared here. That is not tidiness: Zod
 * strips anything a schema does not name, and a field omitted from a proxy
 * schema is permanently `undefined` in the browser with no error raised
 * anywhere. The field lists below were taken from the running backend's
 * OpenAPI document (`GuidanceRuleResponse`, `PersonPreferencesResponse`,
 * `GuidancePreviewResponse`) rather than from reading the Python by eye.
 */

// -- enums -------------------------------------------------------------------

export const GuidanceLayerSchema = z.enum(["org", "project", "person"]);
export type GuidanceLayer = z.infer<typeof GuidanceLayerSchema>;

export const GuidanceKindSchema = z.enum([
  "rule",
  "vocabulary",
  "evidence_rule",
  "output_rule",
  "style",
]);
export type GuidanceKind = z.infer<typeof GuidanceKindSchema>;

export const GuidanceStatusSchema = z.enum(["active", "proposed", "superseded"]);
export type GuidanceStatus = z.infer<typeof GuidanceStatusSchema>;

export const StyleAudienceSchema = z.enum([
  "executive",
  "business",
  "engineering",
  "operations",
]);
export type StyleAudience = z.infer<typeof StyleAudienceSchema>;

export const StyleToneSchema = z.enum(["neutral", "concise", "explanatory"]);
export type StyleTone = z.infer<typeof StyleToneSchema>;

export const TechnicalDepthSchema = z.enum(["summary", "standard", "deep"]);
export type TechnicalDepth = z.infer<typeof TechnicalDepthSchema>;

export const OutputOrganizationSchema = z.enum([
  "direct_answer",
  "sectioned",
  "incident_report",
]);
export type OutputOrganization = z.infer<typeof OutputOrganizationSchema>;

export const FormattingOptionSchema = z.enum([
  "markdown",
  "bulleted_lists",
  "tables",
  "code_blocks",
]);
export type FormattingOption = z.infer<typeof FormattingOptionSchema>;

export const CommunicationOptionSchema = z.enum([
  "qualify_uncertainty",
  "action_oriented",
  "include_sources",
]);
export type CommunicationOption = z.infer<typeof CommunicationOptionSchema>;

// -- responses ---------------------------------------------------------------

/**
 * One stored rule. All twelve published fields are declared; `status` and
 * `source` are the two the UI branches on (proposed vs active, learned vs
 * typed) and dropping either would make a proposed rule indistinguishable from
 * one in force.
 */
export const GuidanceRuleSchema = z.object({
  id: z.number().nullable().default(null),
  layer: GuidanceLayerSchema,
  kind: GuidanceKindSchema,
  project_id: z.string().nullable().default(null),
  user_id: z.number().nullable().default(null),
  content: z.string().nullable().default(null),
  payload: z.record(z.unknown()).nullable().default(null),
  status: GuidanceStatusSchema,
  source: z.string(),
  supersedes_id: z.number().nullable().default(null),
  created_by: z.number().nullable().default(null),
  created_at: z.string().nullable().default(null),
});
export type GuidanceRule = z.infer<typeof GuidanceRuleSchema>;

export const GuidanceRuleListResponseSchema = z.object({
  rules: z.array(GuidanceRuleSchema).default([]),
});
export type GuidanceRuleListResponse = z.infer<typeof GuidanceRuleListResponseSchema>;

/** The PERSON overlay, in the shape JSON carries it. Used for read and write. */
export const StylePreferenceSchema = z.object({
  audience: StyleAudienceSchema.nullable().default(null),
  tone: StyleToneSchema.nullable().default(null),
  technical_depth: TechnicalDepthSchema.nullable().default(null),
  output_organization: OutputOrganizationSchema.nullable().default(null),
  formatting: z.array(FormattingOptionSchema).default([]),
  communication: z.array(CommunicationOptionSchema).default([]),
});
export type StylePreference = z.infer<typeof StylePreferenceSchema>;

export const PersonPreferencesResponseSchema = z.object({
  project_id: z.string(),
  user_id: z.number(),
  style: StylePreferenceSchema.nullable().default(null),
  rules: z.array(GuidanceRuleSchema).default([]),
});
export type PersonPreferencesResponse = z.infer<typeof PersonPreferencesResponseSchema>;

export const GuidancePreviewResponseSchema = z.object({
  project_id: z.string(),
  user_id: z.number(),
  rendered: z.string(),
  cache_key: z.string(),
  conflicts: z.array(z.string()).default([]),
  layer_tokens: z.record(z.number()).default({}),
  layer_budgets: z.record(z.number()).default({}),
});
export type GuidancePreviewResponse = z.infer<typeof GuidancePreviewResponseSchema>;

// -- the workspace ----------------------------------------------------------

/**
 * What produced a file in the workspace.
 *
 * Declared as a plain string rather than an enum on purpose. `document:<id>` is
 * open-ended, and a value the backend adds later must not make the whole
 * workspace unparseable -- a tree that fails to render because one file has an
 * unfamiliar provenance is a strictly worse outcome than a file labelled with a
 * raw token. `describeWorkspaceOrigin` does the narrowing at the point of
 * display, where an unknown value costs nothing.
 */
export const WORKSPACE_LAYER_ORIGINS = [
  "assembled",
  // The agent's system role, projected as `.nexus/SYSTEM_PROMPT.md`. It is the
  // one always-loaded file whose bytes are NOT inside AGENTS.md -- they reach
  // the model through opencode.json, which is never projected because it carries
  // the MCP bearer. Absent from this list it rendered as the raw token
  // `system_prompt`, in the one view whose whole purpose is showing a user what
  // their assistant was actually told.
  "system_prompt",
  "org_layer",
  "project_layer",
  "person_layer",
  "thread",
  "manifest",
] as const;
export type WorkspaceLayerOrigin = (typeof WORKSPACE_LAYER_ORIGINS)[number];
export type WorkspaceOrigin = WorkspaceLayerOrigin | `document:${number}`;

/**
 * One file in the conversation workspace, as the agent receives it.
 *
 * All five published fields are declared. `always_loaded` is the one the whole
 * view turns on -- it is the difference between text charged on every single
 * turn and text that costs nothing until the agent chooses to open it -- and an
 * undeclared boolean would arrive as `undefined`, which reads as false and would
 * quietly under-report the per-turn cost of the entire workspace.
 *
 * `content` is nullable: a listing may legitimately omit the body of a file, and
 * the viewer says so rather than rendering an empty pane that looks like an
 * empty file.
 */
export const WorkspaceFileSchema = z.object({
  path: z.string(),
  origin: z.string(),
  always_loaded: z.boolean(),
  bytes: z.number(),
  content: z.string().nullable().default(null),
});
export type WorkspaceFile = z.infer<typeof WorkspaceFileSchema>;

export const ProjectWorkspaceResponseSchema = z.object({
  project_id: z.string(),
  files: z.array(WorkspaceFileSchema).default([]),
  docs_mounted: z.boolean().default(false),
  always_loaded_tokens: z.number().default(0),
});
export type ProjectWorkspaceResponse = z.infer<typeof ProjectWorkspaceResponseSchema>;

export const WORKSPACE_ORIGIN_LABELS: Record<WorkspaceLayerOrigin, string> = {
  assembled: "Assembled",
  system_prompt: "System instructions",
  org_layer: "Organization layer",
  project_layer: "Project layer",
  person_layer: "Your preferences",
  thread: "Thread",
  manifest: "Manifest",
};

const DOCUMENT_ORIGIN_PATTERN = /^document:(\d+)$/;

/** The document id an origin names, or null when it names a layer. */
export function workspaceOriginDocumentId(origin: string): number | null {
  const match = DOCUMENT_ORIGIN_PATTERN.exec(origin);
  return match ? Number(match[1]) : null;
}

/** A readable provenance for one file. Unknown values are shown verbatim. */
export function describeWorkspaceOrigin(origin: string): string {
  const documentId = workspaceOriginDocumentId(origin);
  if (documentId !== null) return `Uploaded document #${documentId}`;
  return WORKSPACE_ORIGIN_LABELS[origin as WorkspaceLayerOrigin] ?? origin;
}

/**
 * Why a file is not a second, independent piece of guidance.
 *
 * Two files in the tree restate text that is already somewhere else in it:
 * `AGENTS.md` is assembled from the layer files below it, and `MANIFEST.md` is
 * the same material list `AGENTS.md` already carries. Presenting either as an
 * independent source would double-count the guidance a reader thinks is in
 * force -- and, for `AGENTS.md`, double-count the per-turn cost as well.
 */
export function workspaceRestatementNote(file: WorkspaceFile): string | null {
  if (file.origin === "assembled") {
    return "Built from the layer files below it in this tree. Reading it is reading them again, not extra guidance.";
  }
  if (file.origin === "manifest") {
    return "The same material list AGENTS.md already carries. It costs nothing until something opens it -- it exists to be re-read, not to add anything.";
  }
  return null;
}

/** The layer editor a workspace file belongs to, when it has one. */
export const WORKSPACE_PATH_TO_LAYER: Record<string, GuidanceLayer> = {
  ".nexus/org/RULES.md": "org",
  ".nexus/project/RULES.md": "project",
  ".nexus/user/PREFERENCES.md": "person",
};

// -- document routing -------------------------------------------------------

/** What the agent does with a document, mirrored from `MaterialKind`. */
export const MaterialKindSchema = z.enum([
  "evidence",
  "reference",
  "method",
  "constraint",
]);
export type MaterialKind = z.infer<typeof MaterialKindSchema>;

/**
 * Where a document uploaded under one role lands in the workspace.
 *
 * `materializes` and `destination` are independent answers and both are
 * declared: `destination` is the path a mounted file takes, `materializes` says
 * whether a file is written there at all. A source is never written -- it is
 * reached by search -- and a large non-source document is not written either,
 * which is the one outcome users find surprising and the reason
 * `materialize_max_chars` is surfaced beside it.
 */
export const DocumentRoutingEntrySchema = z.object({
  /**
   * Mirrors the backend's own `role: str`, not a closed enum.
   *
   * The table is derived from `DOCUMENT_ROLES`, so a role added there appears
   * here with no backend edit -- and an enum on this side would turn that into
   * a parse failure that empties the whole table rather than one row the form
   * never matches. Narrowing happens where the form looks a role up.
   */
  role: z.string(),
  kind: MaterialKindSchema,
  destination: z.string().nullable().default(null),
  always_loaded: z.boolean(),
  materializes: z.boolean(),
  description: z.string(),
  /** Present only if the backend publishes the ceiling per entry. */
  materialize_max_chars: z.number().nullable().default(null),
});
export type DocumentRoutingEntry = z.infer<typeof DocumentRoutingEntrySchema>;

/**
 * The routing table as this app's proxy publishes it.
 *
 * The proxy normalizes the upstream answer into this envelope so the browser
 * has one shape to read whether the backend returns a bare array or an object
 * carrying the ceiling alongside the rows.
 */
export const DocumentRoutingResponseSchema = z.object({
  routes: z.array(DocumentRoutingEntrySchema).default([]),
  materialize_max_chars: z.number().nullable().default(null),
});
export type DocumentRoutingResponse = z.infer<typeof DocumentRoutingResponseSchema>;

/**
 * Character ceiling above which a document stays search-only, mirrored from
 * `MATERIALIZE_MAX_CHARS` in `src/harness/guidance/material.py`.
 *
 * A fallback for when the endpoint does not publish it. The endpoint is the
 * authority, because it reads the same constant the ingest path enforces.
 */
export const MATERIALIZE_MAX_CHARS = 32_000;

// -- requests ----------------------------------------------------------------

/**
 * A new rule at a level the *route* fixes.
 *
 * `layer`, `project_id` and `user_id` are absent on purpose and the backend
 * forbids extras: a body carrying a scope is making a claim the path already
 * decided, and forwarding it would be refused outright.
 */
export const GuidanceRuleCreateRequestSchema = z.object({
  kind: GuidanceKindSchema,
  content: z.string().max(500).nullable().optional(),
  payload: z.record(z.unknown()).nullable().optional(),
});
export type GuidanceRuleCreateRequest = z.infer<typeof GuidanceRuleCreateRequestSchema>;

/** `ReferenceId` in the pack contract: lowercase, 2-160 chars. */
export const REFERENCE_ID_PATTERN = /^[a-z][a-z0-9_.:-]{1,159}$/;

/** Payload shape for a `vocabulary` rule (`VocabularyTerm`). */
export const VocabularyPayloadSchema = z.object({
  term: z.string().min(1).max(80),
  aliases: z.array(z.string().min(1)).max(20).default([]),
  description: z.string().min(1).max(500),
});
export type VocabularyPayload = z.infer<typeof VocabularyPayloadSchema>;

/** Payload shape for an `evidence_rule` (`EvidenceRankingRule`). */
export const EvidenceRulePayloadSchema = z.object({
  evidence_type: z.string().regex(REFERENCE_ID_PATTERN),
  weight: z.number().int().min(-100).max(100),
  source_kind: z.string().regex(REFERENCE_ID_PATTERN).nullable().default(null),
});
export type EvidenceRulePayload = z.infer<typeof EvidenceRulePayloadSchema>;

// -- constants the UI needs --------------------------------------------------

/**
 * Per-layer ceilings, mirrored from `LAYER_TOKEN_BUDGETS` in
 * `src/harness/guidance/models.py`.
 *
 * Only a fallback for when the preview call has not answered: the preview
 * returns `layer_budgets` and that is the authority, because it is read from
 * the same constant the write path enforces.
 */
export const LAYER_TOKEN_BUDGETS: Record<GuidanceLayer, number> = {
  org: 400,
  project: 600,
  person: 150,
};

/** Free-text kinds. Everything else must supply a `payload`. */
export const TEXT_KINDS: readonly GuidanceKind[] = ["rule", "output_rule"];

export const KIND_LABELS: Record<GuidanceKind, string> = {
  rule: "Rule",
  output_rule: "Output rule",
  vocabulary: "Term",
  evidence_rule: "Evidence weight",
  style: "Style",
};

export const MAX_RULE_CONTENT_CHARS = 500;

export const AUDIENCE_LABELS: Record<StyleAudience, string> = {
  executive: "Executive",
  business: "Business",
  engineering: "Engineering",
  operations: "Operations",
};

export const TONE_LABELS: Record<StyleTone, string> = {
  neutral: "Neutral",
  concise: "Concise",
  explanatory: "Explanatory",
};

export const DEPTH_LABELS: Record<TechnicalDepth, string> = {
  summary: "Summary",
  standard: "Standard",
  deep: "Deep",
};

export const ORGANIZATION_LABELS: Record<OutputOrganization, string> = {
  direct_answer: "Direct answer",
  sectioned: "Sectioned",
  incident_report: "Incident report",
};

export const FORMATTING_LABELS: Record<FormattingOption, string> = {
  markdown: "Markdown",
  bulleted_lists: "Bulleted lists",
  tables: "Tables",
  code_blocks: "Code blocks",
};

export const COMMUNICATION_LABELS: Record<CommunicationOption, string> = {
  qualify_uncertainty: "Qualify uncertainty",
  action_oriented: "Action oriented",
  include_sources: "Include sources",
};

export const AUDIENCE_OPTIONS = StyleAudienceSchema.options;
export const TONE_OPTIONS = StyleToneSchema.options;
export const DEPTH_OPTIONS = TechnicalDepthSchema.options;
export const ORGANIZATION_OPTIONS = OutputOrganizationSchema.options;
export const FORMATTING_OPTIONS = FormattingOptionSchema.options;
export const COMMUNICATION_OPTIONS = CommunicationOptionSchema.options;

/** An empty overlay, used as the starting point of the preferences form. */
export const EMPTY_STYLE_PREFERENCE: StylePreference = {
  audience: null,
  tone: null,
  technical_depth: null,
  output_organization: null,
  formatting: [],
  communication: [],
};

/** True when an overlay sets nothing; the backend refuses to store one. */
export function isEmptyStyle(style: StylePreference): boolean {
  return (
    style.audience === null &&
    style.tone === null &&
    style.technical_depth === null &&
    style.output_organization === null &&
    style.formatting.length === 0 &&
    style.communication.length === 0
  );
}
