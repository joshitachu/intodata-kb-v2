import { z } from "zod";

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
  images: z.array(z.string()).optional(),
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

const StructuredAnswerValueSchema = z.union([
  z.string(),
  z.number(),
  z.boolean(),
  z.null(),
]);

export const StructuredAnswerCitationSchema = z.object({
  evidence_id: z.string(),
  label: z.string(),
  uri: z.string().nullable().optional(),
  source_class: z.string(),
});

export const StructuredAnswerSectionSchema = z.object({
  heading: z.string(),
  body: z.string().nullable().optional(),
  rows: z.array(z.record(StructuredAnswerValueSchema)).optional().default([]),
});

export const StructuredAnswerSchema = z.object({
  output_contract: z.string(),
  headline: z.string(),
  summary: z.string(),
  sections: z.array(StructuredAnswerSectionSchema).optional().default([]),
  limitations: z.array(z.string()).optional().default([]),
  citations: z.array(StructuredAnswerCitationSchema).optional().default([]),
  evidence_refs: z.array(z.string()).optional().default([]),
  answer_status: z.enum(["complete", "partial", "insufficient"]),
  execution_status: z.enum(["completed", "failed", "cancelled", "running"]),
  domain_status: z.enum(["healthy", "degraded", "failed", "unknown"]),
  artifacts: z
    .array(
      z.object({
        label: z.string(),
        url: z.string(),
        kind: z.string().optional(),
      }),
    )
    .optional()
    .default([]),
});

export type StructuredAnswer = z.infer<typeof StructuredAnswerSchema>;

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
  structured_answer: StructuredAnswerSchema.nullable().optional(),
  harness_activities: z
    .array(z.lazy(() => HarnessActivityEventSchema))
    .optional(),
  pending_action: z
    .lazy(() => HarnessPendingActionSchema.nullable())
    .optional(),
  created_at: z.string(),
});

export type MessageResponse = z.infer<typeof MessageResponseSchema>;

export const ConversationResponseSchema = z.object({
  id: z.number(),
  title: z.string().nullable(),
  message_count: z.number(),
  created_at: z.string(),
  updated_at: z.string(),
});

export type ConversationResponse = z.infer<typeof ConversationResponseSchema>;

export const ConversationDetailResponseSchema = z.object({
  id: z.number(),
  title: z.string().nullable(),
  messages: z.array(MessageResponseSchema),
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
});

export type ConversationCreateRequest = z.input<
  typeof ConversationCreateRequestSchema
>;

export const ConversationUpdateRequestSchema = z.object({
  title: z.string().min(1).max(200),
});

export type ConversationUpdateRequest = z.input<
  typeof ConversationUpdateRequestSchema
>;

// --- Project Docs ---

export const DocFileInfoSchema = z.object({
  name: z.string(),
  type: z.string(),
  size_bytes: z.number(),
  has_pdf: z.boolean(),
  modified_at: z.string(),
});

export type DocFileInfo = z.infer<typeof DocFileInfoSchema>;

export const DocListResponseSchema = z.object({
  project_id: z.string(),
  docs: z.array(DocFileInfoSchema),
  total: z.number(),
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
  // PAT connection (fallback). Omitted for GitHub App connections.
  github_token: z.string().min(1).optional(),
  // GitHub App connection (preferred). Set after installing the App.
  installation_id: z.number().int().positive().optional(),
});

export type AddGithubSource = z.infer<typeof AddGithubSourceSchema>;

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
});

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

// UI-state projection of a runtime tool-use call. Live-stream state only; never
// persisted with message history and never carries raw `data` payloads.
export type RuntimeToolStatusValue = "running" | "completed" | "failed";

export interface RuntimeToolProgress {
  toolName: string;
  status: RuntimeToolStatusValue;
  latencyMs: number | null;
  truncated: boolean;
  error: RuntimeToolError | null;
}

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
    workflow_run_id: input.workflow_run_id,
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
  workflow_run_id: z.union([z.string(), z.number().int().positive()]).transform(String),
  interrupt_id: z.number().int().positive(),
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

// --- Harness Packs ---

export const HarnessPackViewSchema = z.object({
  id: z.string(),
  name: z.string(),
  version: z.string(),
  pack_type: z.enum(["documentation", "domain"]),
  includes: z.array(z.string()),
  workflow_labels: z.array(z.string()),
  workflow_descriptions: z.array(z.string()),
  entity_labels: z.array(z.string()),
  documentation_provider_ref: z.string().nullable(),
  content_hash: z.string().regex(/^[a-f0-9]{64}$/),
});
export type HarnessPackView = z.infer<typeof HarnessPackViewSchema>;

export const HarnessPackSnapshotSchema = z.object({
  active_pack_ids: z.array(z.string()),
  resolved_packs: z.array(z.object({
    id: z.string(), version: z.string(), content_hash: z.string().regex(/^[a-f0-9]{64}$/),
  })),
  snapshot_hash: z.string().regex(/^[a-f0-9]{64}$/),
});
export type HarnessPackSnapshot = z.infer<typeof HarnessPackSnapshotSchema>;

export const HarnessPackCatalogSchema = z.object({
  packs: z.array(HarnessPackViewSchema),
  active: HarnessPackSnapshotSchema,
  total: z.number(),
});
export type HarnessPackCatalog = z.infer<typeof HarnessPackCatalogSchema>;

export const HarnessPackRecommendationSchema = z.object({
  pack_id: z.string(),
  confidence: z.number().min(0).max(1),
  reasons: z.array(z.string()).min(1),
});
export type HarnessPackRecommendation = z.infer<typeof HarnessPackRecommendationSchema>;

export const HarnessPackActivationSchema = z.object({
  project_id: z.string(),
  workflow_key: z.string(),
  active_pack_ids: z.array(z.string()),
  snapshot: HarnessPackSnapshotSchema,
});
export type HarnessPackActivation = z.infer<typeof HarnessPackActivationSchema>;

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
} from "./runtime";
