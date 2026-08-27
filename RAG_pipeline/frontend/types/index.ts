import { z } from "zod";
import { PageScopeSchema, TopologyTargetListSchema } from "./topology";

export const DocumentRoleSchema = z.enum([
  "source",
  "reference",
  "guidance",
  "standard",
  "template",
  "policy",
]);

export type DocumentRole = z.infer<typeof DocumentRoleSchema>;

export const DOCUMENT_ROLES: DocumentRole[] = [
  "source",
  "reference",
  "guidance",
  "standard",
  "template",
  "policy",
];

// --- Sources ---

export const DocumentSourceSchema = z.object({
  document_id: z.number(),
  filename: z.string(),
  score: z.number(),
  text: z.string(),
  reference_number: z.number(),
  chunk_indexes: z.array(z.number()).optional().default([]),
  document_role: DocumentRoleSchema.optional().default("source"),
  source_layer: z.string().nullable().optional(),
  display_name: z.string().nullable().optional(),
  visibility: z.string().nullable().optional(),
  project_id: z.string().nullable().optional(),
});

export type DocumentSource = z.infer<typeof DocumentSourceSchema>;

// --- Documents ---

export const DocumentResponseSchema = z.object({
  id: z.number(),
  filename: z.string(),
  file_type: z.string(),
  document_role: DocumentRoleSchema.optional().default("source"),
  source: z.string().nullable(),
  author: z.string().nullable(),
  department: z.string().nullable(),
  project_id: z.string().nullable(),
  chunk_count: z.number(),
  created_at: z.string(),
});

export type DocumentResponse = z.infer<typeof DocumentResponseSchema>;

export const DocumentListResponseSchema = z.object({
  documents: z.array(DocumentResponseSchema),
  total: z.number(),
});

export type DocumentListResponse = z.infer<typeof DocumentListResponseSchema>;

// --- Ingest ---

export const IngestResponseSchema = z.object({
  document_id: z.number(),
  filename: z.string(),
  status: z.string(),
  message: z.string(),
});

export type IngestResponse = z.infer<typeof IngestResponseSchema>;

// --- Token Usage ---

export const UsageStatsSchema = z.object({
  input_tokens: z.number(),
  output_tokens: z.number(),
  model: z.string(),
  provider: z.string(),
  cost_usd: z.number(),
  latency_ms: z.number(),
});

export type UsageStats = z.infer<typeof UsageStatsSchema>;

// --- Agent Steps ---

export const AgentStepSchema = z.object({
  id: z.string(),
  label: z.string(),
  status: z.enum(["running", "completed", "skipped", "failed"]),
});

export type AgentStep = z.infer<typeof AgentStepSchema>;

// --- Conversations ---

export const ConversationMessageRequestSchema = z.object({
  query: z.string().min(1),
  top_k: z.number().int().min(1).max(50).optional().default(10),
  threshold: z.number().min(0).max(1).optional().default(0.5),
  project_id: z.string().optional(),
  images: z.array(z.string()).max(4).optional(),
  client_turn_id: z.string().min(8).max(64).optional(),
  // Where the question was asked. An untrusted prompt hint on the backend --
  // never an access or retrieval filter (see backend `schemas.py`).
  page_scope: PageScopeSchema.optional(),
});

export type ConversationMessageRequest = z.input<
  typeof ConversationMessageRequestSchema
>;

export const SlashCommandSchema = z.object({
  name: z.string().regex(/^[a-z][a-z0-9_-]*$/),
  description: z.string(),
  argument_hint: z.string().nullable().optional(),
});

export type SlashCommand = z.infer<typeof SlashCommandSchema>;

export const SlashCommandListResponseSchema = z.object({
  commands: z.array(SlashCommandSchema),
});

export const WorkflowChildCardSchema = z.object({
  kind: z.string().min(1).max(120),
  status: z.enum(["unknown", "queued", "running", "succeeded", "failed", "cancelled"]),
  error_message: z.string().max(500).nullable().optional(),
}).strict();

export const WorkflowCardSchema = z.object({
  workflow_run_id: z.number().int().positive(),
  project_id: z.string().max(255).nullable().optional(),
  capability_id: z.string().min(1).max(160),
  label: z.string().min(1).max(200),
  status: z.enum([
    "queued",
    "in_progress",
    "needs_input",
    "succeeded",
    "failed",
    "cancelled",
    "indeterminate",
  ]),
  display_label: z.string().min(1).max(80),
  display_tone: z.enum(["neutral", "progress", "success", "danger", "muted", "warning"]),
  is_active: z.boolean(),
  summary: z.string().max(2000).nullable().optional(),
  error_message: z.string().max(500).nullable().optional(),
  started_at: z.string(),
  finished_at: z.string().nullable().optional(),
  projected_at: z.string(),
  elapsed_ms: z.number().int().nonnegative(),
  timing_complete: z.boolean(),
  children: z.array(WorkflowChildCardSchema).max(20).optional().default([]),
  artifact_links: z.array(z.object({
    label: z.string(),
    url: z.string(),
    kind: z.string(),
  }).strict()).max(50).optional().default([]),
}).strict();

export type WorkflowCard = z.infer<typeof WorkflowCardSchema>;

export const TypedResponseFactSchema = z.object({
  fact_id: z.string(),
  kind: z.enum([
    "observation_status",
    "summary",
    "error",
    "data",
    "requested_input",
    "workflow_status",
    "workflow_summary",
    "artifact_presence",
    "evidence_presence",
  ]),
  label: z.string(),
  value: z.string(),
  mandatory: z.boolean().optional().default(false),
  workflow_run_id: z.number().int().positive().nullable().optional(),
}).strict();

export const EvidenceSourceFamilySchema = z.enum([
  "topology",
  "documentation",
  "runtime",
  "metadata",
  "conversation",
  "connector",
  "other",
]);

export type EvidenceSourceFamily = z.infer<typeof EvidenceSourceFamilySchema>;

export const EvidenceSourceRoleSchema = z.enum([
  "structure",
  "explanation",
  "runtime_verification",
  "metadata",
  "conversation_context",
  "external_evidence",
  "supporting",
]);

export const EvidenceSummarySchema = z.object({
  version: z.literal("1.0"),
  evidence_step_count: z.number().int().nonnegative(),
  successful_sources: z.array(z.object({
    family: EvidenceSourceFamilySchema,
    role: EvidenceSourceRoleSchema,
    evidence_count: z.number().int().nonnegative(),
  }).strict()),
  failed_sources: z.array(z.object({
    family: EvidenceSourceFamilySchema,
    status: z.enum(["failed", "unavailable", "denied", "needs_input"]),
    reason_code: z.string().nullable(),
    message: z.string().nullable().optional(),
  }).strict()),
  zero_evidence: z.boolean(),
  documentation: z.object({
    provider_used: z.string().nullable(),
    fallback_used: z.boolean(),
    attempts: z.array(z.object({
      provider: z.string(),
      status: z.enum(["failed", "succeeded"]),
    }).strict()),
  }).strict(),
}).strict();

export type EvidenceSummary = z.infer<typeof EvidenceSummarySchema>;

export const TypedAgentResponseSchema = z.object({
  response_type: z.literal("agent_response_v1"),
  kind: z.enum(["answer", "clarification", "status", "error", "no_progress"]),
  content: z.string(),
  synthesis_source: z.enum(["agent_plan", "deterministic_fallback", "direct"]),
  facts: z.array(TypedResponseFactSchema).max(160).optional().default([]),
  artifact_links: z.array(z.object({
    label: z.string(),
    url: z.string(),
    kind: z.string(),
  }).strict()).max(50).optional().default([]),
  evidence_refs: z.array(z.object({ id: z.string(), label: z.string() }).strict()).max(100).optional().default([]),
  workflow_cards: z.array(WorkflowCardSchema).max(20).optional().default([]),
  evidence_summary: EvidenceSummarySchema.optional(),
  stop_reason: z.string(),
  factual_fingerprint: z.string().regex(/^[a-f0-9]{64}$/),
}).strict();

export type TypedAgentResponse = z.infer<typeof TypedAgentResponseSchema>;

export const GroundedAgentResponseSchema = z.object({
  response_type: z.literal("agent_response_v2_grounded"),
  content: z.string(),
  answer_status: z.enum([
    "grounded",
    "partial",
    "no_evidence",
    "source_unavailable",
    "scope_denied",
    "tool_failed",
    "conflicting",
    "denied",
  ]),
  citations: z.array(z.object({
    claim_key: z.string().min(1).max(200),
    evidence_refs: z.array(z.string()).max(100),
  }).strict()).max(160),
  disclosures: z.array(z.string().max(120)).max(100),
  attribution: z.object({
    runtime_release: z.string().min(1),
    manifest_snapshot_hash: z.string().optional(),
    model_profile: z.string().min(1),
    prompt_version: z.string().optional(),
  }).strict(),
  evidence_summary: EvidenceSummarySchema.optional(),
  topology_targets: TopologyTargetListSchema.optional(),
}).strict();

export type GroundedAgentResponse = z.infer<typeof GroundedAgentResponseSchema>;

export const MessageStructuredAnswerSchema = z.discriminatedUnion(
  "response_type",
  [TypedAgentResponseSchema, GroundedAgentResponseSchema],
);
export type MessageStructuredAnswer = z.infer<typeof MessageStructuredAnswerSchema>;

export const MessageResponseSchema = z.object({
  id: z.number(),
  role: z.string(),
  content: z.string(),
  sources: z.array(DocumentSourceSchema).nullable(),
  image_data: z.array(z.string()).nullable().optional(),
  latency_ms: z.number().nullable(),
  status: z.string().optional().default("completed"),
  error_message: z.string().nullable().optional(),
  workflow_run_id: z.number().int().positive().nullable().optional(),
  turn_plan: z.record(z.unknown()).nullable().optional(),
  structured_answer: MessageStructuredAnswerSchema.nullable().optional(),
  harness_activities: z
    .array(z.lazy(() => HarnessActivityEventSchema))
    .optional(),
  workflow_cards: z.array(WorkflowCardSchema).optional(),
  pending_action: z
    .lazy(() => HarnessPendingActionSchema.nullable())
    .optional(),
  // The surface and level the question was asked from, recorded per message so
  // one shared thread stays readable when questions come from several places.
  page_scope: PageScopeSchema.nullable().optional(),
  created_at: z.string(),
});

export type MessageResponse = z.infer<typeof MessageResponseSchema>;

export const ConversationResponseSchema = z.object({
  id: z.number(),
  title: z.string().nullable(),
  message_count: z.number(),
  origin_surface: z.enum(["chat", "topology"]).optional().default("chat"),
  visible_in_chat: z.boolean().optional().default(true),
  created_at: z.string(),
  updated_at: z.string(),
});

export type ConversationResponse = z.infer<typeof ConversationResponseSchema>;

export const ConversationDetailResponseSchema = z.object({
  id: z.number(),
  title: z.string().nullable(),
  messages: z.array(MessageResponseSchema),
  origin_surface: z.enum(["chat", "topology"]).optional().default("chat"),
  visible_in_chat: z.boolean().optional().default(true),
  agent_plan: z.object({
    plan_id: z.string(), goal: z.string(), success_criteria: z.array(z.string()),
    constraints: z.array(z.string()), scoped_entities: z.array(z.string()),
    status: z.enum(["active", "paused", "abandoned"]), version: z.number(),
    steps: z.array(z.object({ key: z.string(), objective: z.string(), success_criteria: z.array(z.string()), capability_id: z.string().nullable(), kind: z.string(), depends_on: z.array(z.string()), requires_wait: z.boolean(), restart_sensitive: z.boolean(), status: z.enum(["pending", "executing", "awaiting_approval", "waiting", "completed", "failed", "cancelled"]), effect_operation_id: z.string().nullable().optional(), capability_receipt_ref: z.record(z.unknown()).nullable().optional(), workflow_run_id: z.number().int().positive().nullable().optional(), started_at: z.string().nullable().optional(), finished_at: z.string().nullable().optional() })),
    last_change_reason: z.string().nullable(), created_at: z.string(), updated_at: z.string(),
  }).nullable().optional(),
  created_at: z.string(),
  updated_at: z.string(),
});

export type ConversationDetailResponse = z.infer<
  typeof ConversationDetailResponseSchema
>;

export const ConversationListResponseSchema = z.object({
  conversations: z.array(ConversationResponseSchema),
  total: z.number(),
});

export type ConversationListResponse = z.infer<
  typeof ConversationListResponseSchema
>;

export const ConversationCreateRequestSchema = z.object({
  title: z.string().max(200).optional(),
  surface: z.enum(["chat", "topology"]).optional().default("chat"),
});

export type ConversationCreateRequest = z.input<
  typeof ConversationCreateRequestSchema
>;

export const ConversationUpdateRequestSchema = z.object({
  title: z.string().min(1).max(200).optional(),
  visible_in_chat: z.boolean().optional(),
});

export type ConversationUpdateRequest = z.input<
  typeof ConversationUpdateRequestSchema
>;

// --- Project Docs ---

export const DocFileInfoSchema = z.object({
  name: z.string(),
  // Sub-project this doc belongs to; null for a project generated as a single
  // run. A partitioned project emits the same doc names in every sub-project,
  // so `name` alone is not a unique key -- use docKey() below.
  subproject: z.string().nullable().default(null),
  type: z.string(),
  size_bytes: z.number(),
  has_pdf: z.boolean(),
  modified_at: z.string(),
});

export type DocFileInfo = z.infer<typeof DocFileInfoSchema>;

/** Stable identity for a document within a project. */
export function docKey(doc: Pick<DocFileInfo, "name" | "subproject">): string {
  return doc.subproject ? `${doc.subproject}/${doc.name}` : doc.name;
}

export const DocListResponseSchema = z.object({
  project_id: z.string(),
  docs: z.array(DocFileInfoSchema),
  total: z.number(),
  // Sub-projects present in `docs`, in display order. Empty for a flat project,
  // which is the signal to hide the sub-project filter entirely.
  subprojects: z.array(z.string()).default([]),
});

export type DocListResponse = z.infer<typeof DocListResponseSchema>;

export const PdfGenerateResponseSchema = z.object({
  project_id: z.string(),
  status: z.string(),
  converted: z.number(),
  failed: z.number(),
  errors: z.array(z.string()),
});

export type PdfGenerateResponse = z.infer<typeof PdfGenerateResponseSchema>;

export const DocsProjectSchema = z.object({
  id: z.string(),
  display_name: z.string(),
  platform: z.string(),
  doc_count: z.number(),
});

export type DocsProject = z.infer<typeof DocsProjectSchema>;

export const DocsProjectsResponseSchema = z.object({
  projects: z.array(DocsProjectSchema),
  total: z.number(),
});

export type DocsProjectsResponse = z.infer<typeof DocsProjectsResponseSchema>;

// --- Source Projects ---

export const SourceProjectSchema = z.object({
  id: z.string(),
  display_name: z.string(),
  repo_url: z.string().nullable().optional(),
  local_path: z.string().nullable().optional(),
  platform: z.string(),
  source_type: z.string(),
  status: z.string(),
  job_count: z.number().optional().default(0),
  doc_count: z.number().optional().default(0),
  error_message: z.string().nullable().optional(),
  last_scan_at: z.string().nullable().optional(),
  last_generate_at: z.string().nullable().optional(),
  last_rag_push_at: z.string().nullable().optional(),
  auto_reingest_enabled: z.boolean().optional().default(false),
  auto_reingest_last_checked_at: z.string().nullable().optional(),
  auto_reingest_error: z.string().nullable().optional(),
  last_ingested_commit_sha: z.string().nullable().optional(),
  git_update_summary: z.record(z.string(), z.unknown()).nullable().optional(),
  has_github_token: z.boolean().optional().default(false),
  webhook_status: z.string().nullable().optional(),
  // Everything below is returned by /mcp/projects and consumed by the Sources
  // UI. Zod strips keys it does not declare, so omitting one here does not make
  // it "unused" -- it makes it permanently undefined in the browser. That is how
  // `connection_kind` went missing and every Azure DevOps project was rendered
  // with GitHub-only token and webhook controls.
  connection_kind: z.string().nullable().optional(),
  github_account_login: z.string().nullable().optional(),
  azure_devops_connection_id: z.string().nullable().optional(),
  /** Pack the project generates with by default; null means no pack. */
  plugin_pack_id: z.number().nullable().optional(),
  artifact_counts: z
    .object({
      total_artifacts: z.number().optional(),
      job_count: z.number().optional(),
      route_count: z.number().optional(),
      joblet_count: z.number().optional(),
      routelet_count: z.number().optional(),
    })
    .nullable()
    .optional(),
});

export type SourceProject = z.infer<typeof SourceProjectSchema>;

export const SourceProjectsResponseSchema = z.object({
  projects: z.array(SourceProjectSchema),
  total: z.number(),
});

export type SourceProjectsResponse = z.infer<typeof SourceProjectsResponseSchema>;

export const AddGithubSourceSchema = z.object({
  repo_url: z.string().url(),
  project_id: z.string().regex(/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$/),
  platform: z.string().min(1),
  display_name: z.string().trim().min(1).max(160).optional(),
  // Branch to clone. Omitted follows the remote's default branch.
  branch: z.string().trim().min(1).max(255).optional(),
  // PAT connection (fallback). Omitted for GitHub App connections.
  github_token: z.string().min(1).optional(),
  // GitHub App connection (preferred). Set after installing the App.
  installation_id: z.number().int().positive().optional(),
  // Azure DevOps organization connection supplying the clone credential.
  azure_devops_connection_id: z.string().min(1).max(36).optional(),
});

export type AddGithubSource = z.infer<typeof AddGithubSourceSchema>;

// --- Multi-repo projects ---
//
// One project whose source tree is N cloned repositories. Each member becomes
// its own sub-project: its own documentation set and its own generation budget,
// plus a shared cross-repo topology layer.

export const MultiRepoMemberSchema = z.object({
  // The provider's stable name for the repo ("owner/name" on GitHub,
  // "org/project/repo" on Azure DevOps). This is the key a push event resolves
  // against, so it is not a display label and is not URL-shaped.
  repo_identity: z.string().trim().min(1).max(500),
  repo_name: z.string().trim().min(1).max(255),
  clone_url: z.string().url().max(500),
  // Omitted means "whatever the clone resolves to" — i.e. the default branch,
  // which then keeps following upstream changes to the default.
  branch: z.string().trim().min(1).max(255).optional(),
});

export const CreateMultiRepoProjectSchema = z.object({
  project_id: z.string().regex(/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$/),
  display_name: z.string().trim().min(1).max(255).optional(),
  platform: z.string().min(1),
  provider: z.enum(["github", "azure_devops"]),
  members: z.array(MultiRepoMemberSchema).min(1).max(100),
  installation_id: z.number().int().positive().optional(),
  azure_devops_connection_id: z.string().min(1).max(36).optional(),
});

export type CreateMultiRepoProject = z.infer<typeof CreateMultiRepoProjectSchema>;

export const UpdateGithubTokenSchema = z.object({
  github_token: z.string().min(1),
});

export type UpdateGithubToken = z.infer<typeof UpdateGithubTokenSchema>;

// --- GitHub App connection ---

export const GithubAppConnectResponseSchema = z.object({
  install_url: z.string().url(),
});

export type GithubAppConnectResponse = z.infer<typeof GithubAppConnectResponseSchema>;

export const GithubAppRepoItemSchema = z.object({
  full_name: z.string().nullable().optional(),
  clone_url: z.string().nullable().optional(),
  private: z.boolean().nullable().optional(),
  default_branch: z.string().nullable().optional(),
});

export const GithubAppReposResponseSchema = z.object({
  installation_id: z.number(),
  account_login: z.string().nullable().optional(),
  repositories: z.array(GithubAppRepoItemSchema),
});

export type GithubAppReposResponse = z.infer<typeof GithubAppReposResponseSchema>;
export type GithubAppRepoItem = z.infer<typeof GithubAppRepoItemSchema>;

// --- Azure DevOps connection ---
//
// Azure DevOps has no GitHub-App equivalent, so the credential is a Personal
// Access Token entered once per organization. The PAT crosses this boundary on
// the way in only; nothing that comes back ever carries it.

export const ConnectAzureDevOpsSchema = z.object({
  // Accepts organization, project and repository URLs; the server normalizes.
  organization_url: z.string().trim().min(1).max(500),
  pat: z.string().min(1).max(1000),
});

export type ConnectAzureDevOps = z.infer<typeof ConnectAzureDevOpsSchema>;

export const AzureDevOpsConnectionItemSchema = z.object({
  id: z.string(),
  organization_url: z.string(),
  organization_name: z.string(),
  account_name: z.string().nullable().optional(),
  last_verified_at: z.string().nullable().optional(),
});

export type AzureDevOpsConnectionItem = z.infer<typeof AzureDevOpsConnectionItemSchema>;

export const AzureDevOpsConnectionsResponseSchema = z.object({
  connections: z.array(AzureDevOpsConnectionItemSchema),
});

export type AzureDevOpsConnectionsResponse = z.infer<
  typeof AzureDevOpsConnectionsResponseSchema
>;

export const AzureDevOpsRepoItemSchema = z.object({
  id: z.string(),
  name: z.string(),
  project: z.string(),
  clone_url: z.string().nullable().optional(),
  web_url: z.string().nullable().optional(),
  default_branch: z.string().nullable().optional(),
  is_disabled: z.boolean().optional(),
  size_bytes: z.number().optional(),
});

export type AzureDevOpsRepoItem = z.infer<typeof AzureDevOpsRepoItemSchema>;

export const AzureDevOpsReposResponseSchema = z.object({
  connection_id: z.string(),
  organization_name: z.string(),
  repositories: z.array(AzureDevOpsRepoItemSchema),
});

export type AzureDevOpsReposResponse = z.infer<typeof AzureDevOpsReposResponseSchema>;

// --- User Projects (RAG project selector) ---

export const UserProjectSchema = z.object({
  id: z.string(),
  display_name: z.string(),
  platform: z.string(),
});

export type UserProject = z.infer<typeof UserProjectSchema>;

export const UserProjectsResponseSchema = z.object({
  projects: z.array(UserProjectSchema),
});

export type UserProjectsResponse = z.infer<typeof UserProjectsResponseSchema>;

// --- Deep Dive ---

export const DeepDiveRequestSchema = z.object({
  project_id: z.string().min(1),
  question: z.string().min(3),
  session_id: z.string().optional(),
});

export type DeepDiveRequest = z.infer<typeof DeepDiveRequestSchema>;

export const DeepDiveProjectSchema = z.object({
  id: z.string(),
  display_name: z.string(),
  platform: z.string(),
  job_count: z.number(),
  artifact_counts: z.object({
    total_artifacts: z.number().optional(),
    job_count: z.number().optional(),
    route_count: z.number().optional(),
    joblet_count: z.number().optional(),
    routelet_count: z.number().optional(),
  }).optional(),
  last_scan_at: z.string().nullable(),
});

export type DeepDiveProject = z.infer<typeof DeepDiveProjectSchema>;

export const DeepDiveProjectsResponseSchema = z.object({
  projects: z.array(DeepDiveProjectSchema),
  total: z.number(),
});

export type DeepDiveProjectsResponse = z.infer<
  typeof DeepDiveProjectsResponseSchema
>;

// --- SSE stream ---

export const SseDoneSchema = z.object({
  message_id: z.number().int(),
  latency_ms: z.number(),
  run_id: z.string().nullable().optional(),
  replayed: z.boolean().optional().default(false),
});

export const PublicAgentEventSchema = z.object({
  event_id: z.string(),
  run_id: z.string(),
  sequence: z.number().int().positive(),
  type: z.string(),
  created_at: z.string(),
  public_payload: z.record(z.string(), z.unknown()),
  replayable: z.boolean(),
}).strict();

export type PublicAgentEvent = z.infer<typeof PublicAgentEventSchema>;

// --- Auth user ---

export const AuthUserSchema = z.object({
  id: z.number(),
  username: z.string(),
  email: z.string(),
  is_active: z.boolean(),
  is_superadmin: z.boolean(),
  roles: z.array(z.string()),
  permissions: z.array(z.string()),
  onboarding_completed: z.boolean(),
  tour_completed: z.boolean(),
  mfa_enabled: z.boolean(),
  last_login_at: z.string().nullable(),
  has_password: z.boolean(),
  sso_provider: z.string().nullable(),
});

// --- Runtime status (EP-004 slice 1) ---

export const RuntimeStatusResponseSchema = z.object({
  status: z.enum(["disabled", "connected", "unavailable"]),
  provider_key: z.string().nullable(),
  provider_label: z.string(),
  message: z.string(),
  checked_at: z.string(),
});

export type RuntimeStatusResponse = z.infer<typeof RuntimeStatusResponseSchema>;

// --- Runtime tool-use stream events (EP-004 slice 3 backend, slice 4 frontend) ---

export const RuntimeToolCallEventSchema = z.object({
  tool_name: z.string(),
  arguments: z.record(z.string(), z.unknown()).optional().default({}),
});

export type RuntimeToolCallEvent = z.infer<typeof RuntimeToolCallEventSchema>;

export const RuntimeToolErrorSchema = z.object({
  code: z.string(),
  message: z.string(),
});

export type RuntimeToolError = z.infer<typeof RuntimeToolErrorSchema>;

export const RuntimeToolResultEventSchema = z.object({
  tool_name: z.string(),
  success: z.boolean(),
  data: z.unknown().optional(),
  error: RuntimeToolErrorSchema.nullable().optional(),
  latency_ms: z.number(),
  truncated: z.boolean().optional().default(false),
});

export type RuntimeToolResultEvent = z.infer<typeof RuntimeToolResultEventSchema>;

// --- Generic harness activity stream events ---

export const HarnessActivityStatusSchema = z.preprocess((value) => {
  if (typeof value !== "string") return value;
  const normalized = value.toLowerCase();
  if (
    [
      "pending",
      "queued",
      "started",
      "triggered",
      "in_progress",
      "in-progress",
      "running",
    ].includes(normalized)
  ) {
    return "running";
  }
  if (
    [
      "complete",
      "completed",
      "done",
      "resolved",
      "selected",
      "success",
      "succeeded",
    ].includes(normalized)
  ) {
    return "completed";
  }
  if (["blocked", "error", "errored", "failed", "failure"].includes(normalized)) {
    return "failed";
  }
  if (["cancelled", "canceled", "skip", "skipped"].includes(normalized)) {
    return "skipped";
  }
  return normalized;
}, z.enum(["running", "completed", "skipped", "failed"]));

export type HarnessActivityStatus = z.infer<
  typeof HarnessActivityStatusSchema
>;

export const HarnessSafeDetailsValueSchema = z.union([
  z.string(),
  z.number(),
  z.boolean(),
  z.null(),
  z.array(z.union([z.string(), z.number(), z.boolean(), z.null()])),
]);

export const HarnessActivityEventSchema = z.object({
  workflow_run_id: z.string().nullable().optional(),
  event_type: z.string(),
  label: z.string(),
  status: HarnessActivityStatusSchema,
  summary: z.string().nullable().optional(),
  safe_details: z.record(HarnessSafeDetailsValueSchema).optional().default({}),
  timestamp: z.string().nullable().optional(),
  workflow_status: z.string().nullable().optional(),
  workflow_started_at: z.string().nullable().optional(),
  workflow_finished_at: z.string().nullable().optional(),
});

export type HarnessActivityEvent = z.infer<
  typeof HarnessActivityEventSchema
>;

// --- Harness pending actions ---

export const HarnessPendingActionChoiceSchema = z.object({
  label: z.string().trim().min(1).max(160),
  value: z.string().trim().min(1).max(500),
});

export const HarnessPendingActionSchema = z.preprocess((value) => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return value;
  const input = value as Record<string, unknown>;
  return {
    conversation_id: input.conversation_id,
    workflow_run_id: input.workflow_run_id,
    proposal_id: input.proposal_id,
    interrupt_id: input.interrupt_id ?? input.id,
    expected_version: input.expected_version,
    kind: input.kind ?? input.interrupt_type,
    prompt: input.prompt,
    choices: input.choices ?? input.options,
    required_fields: input.required_fields,
    project_id: input.project_id,
    platform: input.platform,
    expires_at: input.expires_at,
  };
}, z.object({
  conversation_id: z.number().int().positive().optional(),
  workflow_run_id: z.union([z.string(), z.number().int().positive()]).transform(String).optional(),
  interrupt_id: z.number().int().positive().optional(),
  proposal_id: z.string().trim().min(1).max(64).optional(),
  expected_version: z.number().int().positive(),
  kind: z.enum(["clarification", "approval"]),
  prompt: z.string().trim().min(1).max(1000),
  choices: z.array(HarnessPendingActionChoiceSchema).max(20).optional().default([]),
  required_fields: z.array(z.string().trim().min(1).max(80)).max(20).optional().default([]),
  project_id: z.string().trim().min(1).max(255).nullable().optional(),
  platform: z.string().trim().min(1).max(80).nullable().optional(),
  expires_at: z.string().nullable().optional(),
}));

export type HarnessPendingAction = z.infer<typeof HarnessPendingActionSchema>;

export const HarnessResumeRequestSchema = z.object({
  interrupt_id: z.number().int().positive(),
  expected_version: z.number().int().positive(),
  response: z.string().trim().min(1).max(4096),
});

export const HarnessCancelRequestSchema = z.object({
  expected_version: z.number().int().positive(),
});

export type HarnessResumeRequest = z.infer<typeof HarnessResumeRequestSchema>;

// --- Governed Harness Packs ---

export const GovernedPackPhaseSchema = z.enum(["foundation", "functional", "style"]);
export const GovernancePackSchema = z.object({
  id: z.string(),
  name: z.string(),
  version: z.string(),
  content_hash: z.string().regex(/^[a-f0-9]{64}$/),
  pack_type: z.enum(["documentation", "domain", "style"]),
  phase: GovernedPackPhaseSchema,
  authority: z.string(),
  trust: z.string(),
  mutability: z.string(),
  scope: z.string(),
  owner: z.string(),
  quality_score: z.number().min(0).max(1).nullable(),
  evaluation_score: z.number().min(0).max(1).nullable(),
  includes: z.array(z.string()),
  workflow_labels: z.array(z.string()),
  workflow_descriptions: z.array(z.string()),
  entity_labels: z.array(z.string()),
  domains: z.array(z.string()).optional(),
  default_domains: z.array(z.string()).optional(),
  documentation_provider_ref: z.string().nullable(),
  blocked_override_rules: z.array(z.string()),
}).transform(({ domains, default_domains, ...pack }) => ({
  ...pack,
  domains: domains ?? default_domains ?? [],
}));
export type GovernancePack = z.infer<typeof GovernancePackSchema>;

export const GovernancePolicySchema = z.object({
  id: z.string(),
  required_certified_pack_ids: z.array(z.string()),
  allowed_functional_pack_ids: z.array(z.string()),
  permitted_style: z.object({
    permitted_fields: z.array(z.string()),
    allowed_audiences: z.array(z.string()),
    allowed_tones: z.array(z.string()),
    allowed_technical_depths: z.array(z.string()),
    allowed_output_organizations: z.array(z.string()),
    allowed_formatting: z.array(z.string()),
    allowed_communication: z.array(z.string()),
  }),
  minimum_quality: z.object({ quality_score: z.number(), evaluation_score: z.number() }),
  protected_invariants: z.array(z.string()),
});
export type GovernancePolicy = z.infer<typeof GovernancePolicySchema>;

export const StylePresentationSchema = z.object({
  audience: z.enum(["executive", "business", "engineering", "operations"]).nullable().optional(),
  tone: z.enum(["neutral", "concise", "explanatory"]).nullable().optional(),
  technical_depth: z.enum(["summary", "standard", "deep"]).nullable().optional(),
  output_organization: z.enum(["direct_answer", "sectioned", "incident_report"]).nullable().optional(),
  formatting: z.array(z.enum(["markdown", "bulleted_lists", "tables", "code_blocks"])),
  communication: z.array(z.enum(["qualify_uncertainty", "action_oriented", "include_sources"])),
});
export type StylePresentation = z.infer<typeof StylePresentationSchema>;

export const StylePackRequestSchema = z.object({
  id: z.string().regex(/^[a-z][a-z0-9_.-]{6,79}$/),
  name: z.string().trim().min(1).max(120),
  version: z.string().regex(/^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][a-zA-Z0-9.-]+)?$/),
  presentation: StylePresentationSchema,
});
export type StylePackRequest = z.infer<typeof StylePackRequestSchema>;

export const StylePackSchema = z.object({
  pack: GovernancePackSchema,
  presentation: StylePresentationSchema,
  project_id: z.string().nullable(),
  owner_user_id: z.number().int(),
  quality_score: z.number().min(0).max(1).nullable(),
  evaluation_score: z.number().min(0).max(1).nullable(),
  evaluation_metadata: z.record(z.unknown()).nullable(),
  review_status: z.string(),
  promotion_eligible: z.boolean(),
});
export type StylePack = z.infer<typeof StylePackSchema>;

export const GovernedPackSnapshotSchema = z.object({
  active_pack_ids: z.array(z.string()),
  resolved_packs: z.array(z.object({
    id: z.string(), version: z.string(), content_hash: z.string().regex(/^[a-f0-9]{64}$/),
  })),
  snapshot_hash: z.string().regex(/^[a-f0-9]{64}$/),
});
export type GovernedPackSnapshot = z.infer<typeof GovernedPackSnapshotSchema>;

export const GovernedPackSelectionSchema = z.object({
  foundation_pack_ids: z.array(z.string()),
  functional_pack_ids: z.array(z.string()),
  style_pack_ids: z.array(z.string()),
});
export type GovernedPackSelection = z.infer<typeof GovernedPackSelectionSchema>;

export const GovernedPackActivationSchema = z.object({
  project_id: z.string(),
  workflow_key: z.string(),
  active_pack_ids: z.array(z.string()),
  selection: GovernedPackSelectionSchema,
  snapshot: GovernedPackSnapshotSchema,
  compiled_snapshot: z.record(z.unknown()),
  presentation: StylePresentationSchema.nullable(),
  packs: z.array(GovernancePackSchema).default([]),
});
export type GovernedPackActivation = z.infer<typeof GovernedPackActivationSchema>;

export const DocumentationPackSchema = z.object({
  id: z.number().int().positive(),
  name: z.string().trim().min(1),
  version: z.string().trim().min(1),
  description: z.string().trim().min(1).nullable(),
});
export type DocumentationPack = z.infer<typeof DocumentationPackSchema>;

export const DocumentationPackCatalogSchema = z.object({
  packs: z.array(DocumentationPackSchema),
});
export type DocumentationPackCatalog = z.infer<typeof DocumentationPackCatalogSchema>;

export const DocumentationPackSelectionSchema = z.object({
  project_id: z.string().trim().min(1),
  selected_plugin_pack_id: z.number().int().positive().nullable(),
  packs: z.array(DocumentationPackSchema),
});
export type DocumentationPackSelection = z.infer<typeof DocumentationPackSelectionSchema>;

export const DocumentationPackDocumentDraftSchema = z.object({
  name: z.string().trim().regex(/^[A-Za-z0-9][A-Za-z0-9_-]*$/).max(80),
  sections: z.array(z.string().trim().min(1).max(200)).max(30),
  instructions: z.string().trim().max(8000).nullable().optional(),
});
export type DocumentationPackDocumentDraft = z.infer<typeof DocumentationPackDocumentDraftSchema>;

export const CreateDocumentationPackRequestSchema = z.object({
  name: z.string().trim().min(3).max(100).regex(/^[A-Za-z0-9][A-Za-z0-9 _-]*$/),
  description: z.string().trim().max(1000).nullable().optional(),
  documents: z.array(DocumentationPackDocumentDraftSchema).min(1).max(30),
});
export type CreateDocumentationPackRequest = z.infer<typeof CreateDocumentationPackRequestSchema>;

// --- Runtime logs API (EP-005 slice 2 contract; schemas in ./runtime) ---
export {
  RuntimeExecutionStateSchema,
  RuntimeLogSeveritySchema,
  RuntimeExecutionSchema,
  RuntimeFailureSchema,
  RuntimeLogEventSchema,
  ExecutionListResponseSchema,
  ExecutionDetailResponseSchema,
  LogEventListResponseSchema,
  FailureListResponseSchema,
  RuntimeHealthSummarySchema,
  HealthSummaryResponseSchema,
  RuntimeSyncStatusSchema,
  RuntimeEngineStatusSchema,
  RuntimeSourceSchema,
  RuntimeEngineSchema,
  RuntimeEstateOverviewSchema,
  RuntimeSourceListResponseSchema,
  RuntimeEngineListResponseSchema,
  RuntimeOverviewResponseSchema,
  RuntimeJobSchema,
  RuntimeJobRunSchema,
  RuntimeJobHealthSchema,
  RuntimeEngineHealthSchema,
  RuntimeJobListResponseSchema,
  RuntimeArtifactSchema,
  RuntimeConnectionSchema,
  RuntimeJobDependencySchema,
  RuntimeExtendedMetadataAvailabilitySchema,
  RuntimeDependencySummarySchema,
  RuntimeArtifactListResponseSchema,
  RuntimeConnectionListResponseSchema,
  RuntimeDependencyListResponseSchema,
  RuntimeDependencySummaryResponseSchema,
  RuntimeJobRunListResponseSchema,
  RuntimeJobHealthResponseSchema,
  RuntimeEngineHealthResponseSchema,
  RuntimePlanStepSchema,
  RuntimePlanSchema,
  RuntimePlanRunStepResultSchema,
  RuntimePlanRunSchema,
  RuntimePlanHealthSchema,
  RuntimePlanListResponseSchema,
  RuntimePlanRunListResponseSchema,
  RuntimePlanHealthResponseSchema,
  // Smart-log insight schemas (EP-006 slice 2)
  RuntimeFailureEstateContextSchema,
  RuntimeExecutionSummaryInsightSchema,
  ExecutionSummaryResponseSchema,
  RuntimeTimelineEventSchema,
  TimelineResponseSchema,
  RuntimeIncidentInsightSchema,
  IncidentListResponseSchema,
  IncidentDetailResponseSchema,
  RuntimeIncidentEvidenceSchema,
  IncidentEvidenceResponseSchema,
  // Similar failures schemas (EP-006 slice 5)
  RuntimeSimilarIncidentSchema,
  RuntimeAlertReadyMetadataSchema,
  IncidentSimilarResponseSchema,
  // Aggregate rollup schemas
  RuntimeTrendBucketSchema,
  RuntimeOutcomeTrendPointSchema,
  RuntimeTrendCoverageSchema,
  RuntimeTrendResponseSchema,
  RuntimeJobReliabilitySchema,
  RuntimeJobReliabilityResponseSchema,
  RuntimeJobInsightSchema,
  RuntimeJobInsightResponseSchema,
  RuntimeComponentHotspotSchema,
  RuntimeComponentHotspotResponseSchema,
  RuntimeJobThroughputSchema,
  RuntimeJobThroughputResponseSchema,
  RuntimeErrorSignatureSchema,
  RuntimeErrorSignatureResponseSchema,
} from "./runtime";
export type {
  RuntimeExecutionState,
  RuntimeLogSeverity,
  RuntimeExecution,
  RuntimeFailure,
  RuntimeLogEvent,
  ExecutionListResponse,
  ExecutionDetailResponse,
  LogEventListResponse,
  FailureListResponse,
  RuntimeHealthSummary,
  HealthSummaryResponse,
  RuntimeSyncStatus,
  RuntimeEngineStatus,
  RuntimeSource,
  RuntimeEngine,
  RuntimeEstateOverview,
  RuntimeSourceListResponse,
  RuntimeEngineListResponse,
  RuntimeOverviewResponse,
  RuntimeJob,
  RuntimeArtifact,
  RuntimeConnection,
  RuntimeJobDependency,
  RuntimeExtendedMetadataAvailability,
  RuntimeDependencySummary,
  RuntimeArtifactListResponse,
  RuntimeConnectionListResponse,
  RuntimeDependencyListResponse,
  RuntimeDependencySummaryResponse,
  RuntimeJobRun,
  RuntimeJobHealth,
  RuntimeEngineHealth,
  RuntimeJobListResponse,
  RuntimeJobRunListResponse,
  RuntimeJobHealthResponse,
  RuntimeEngineHealthResponse,
  RuntimePlanStep,
  RuntimePlan,
  RuntimePlanRunStepResult,
  RuntimePlanRun,
  RuntimePlanHealth,
  RuntimePlanListResponse,
  RuntimePlanRunListResponse,
  RuntimePlanHealthResponse,
  // Smart-log insight types (EP-006 slice 2)
  RuntimeFailureEstateContext,
  RuntimeExecutionSummaryInsight,
  ExecutionSummaryResponse,
  RuntimeTimelineEvent,
  TimelineResponse,
  RuntimeIncidentInsight,
  IncidentListResponse,
  IncidentDetailResponse,
  RuntimeIncidentEvidence,
  IncidentEvidenceResponse,
  // Similar failures types (EP-006 slice 5)
  RuntimeSimilarIncident,
  RuntimeAlertReadyMetadata,
  IncidentSimilarResponse,
  // Aggregate rollup types
  RuntimeTrendBucket,
  RuntimeOutcomeTrendPoint,
  RuntimeTrendCoverage,
  RuntimeTrendResponse,
  RuntimeJobReliability,
  RuntimeJobReliabilityResponse,
  RuntimeJobInsight,
  RuntimeJobInsightResponse,
  RuntimeComponentHotspot,
  RuntimeComponentHotspotResponse,
  RuntimeJobThroughput,
  RuntimeJobThroughputResponse,
  RuntimeErrorSignature,
  RuntimeErrorSignatureResponse,
} from "./runtime";

// --- Topology level-2 drill-down (S1 contract; schemas in ./jobGraph) ---

export {
  ComponentRoleSchema,
  JobGraphNodeSchema,
  JobGraphEdgeSchema,
  TmapTablesSchema,
  JobGraphResponseSchema,
} from "./jobGraph";
export type {
  ComponentRole,
  ParamValue,
  JobGraphNode,
  JobGraphEdge,
  TmapTables,
  JobGraphResponse,
} from "./jobGraph";

// --- Topology level-3 drill-down (S2 contract; schemas in ./componentGraph) ---

export {
  ComponentGraphTableSchema,
  ComponentGraphEdgeSchema,
  ComponentGraphResponseSchema,
  ComponentGraphFallbackSchema,
  RESOLVED_CONFIDENCE_VALUES,
} from "./componentGraph";
export type {
  ComponentGraphTable,
  ComponentGraphEdge,
  ComponentGraphResponse,
  ComponentGraphFallback,
} from "./componentGraph";

// --- Job detail (existing endpoint; schemas in ./jobDetail) ---

export {
  ComponentParamSchema,
  ScanComponentSchema,
  JobScanDataSchema,
  JobDocSectionSchema,
  JobDetailResponseSchema,
} from "./jobDetail";
export type {
  ComponentParam,
  ScanComponent,
  JobScanData,
  JobDocSection,
  JobDetailResponse,
} from "./jobDetail";
