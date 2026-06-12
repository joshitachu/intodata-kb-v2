import { z } from "zod";

// --- Sources ---

export const DocumentSourceSchema = z.object({
  document_id: z.number(),
  filename: z.string(),
  score: z.number(),
  text: z.string(),
  reference_number: z.number(),
  chunk_indexes: z.array(z.number()).optional().default([]),
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

export const MessageResponseSchema = z.object({
  id: z.number(),
  role: z.string(),
  content: z.string(),
  sources: z.array(DocumentSourceSchema).nullable(),
  image_data: z.array(z.string()).nullable().optional(),
  latency_ms: z.number().nullable(),
  status: z.string().optional().default("completed"),
  error_message: z.string().nullable().optional(),
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
  github_token: z.string().min(1),
});

export type AddGithubSource = z.infer<typeof AddGithubSourceSchema>;

export const UpdateGithubTokenSchema = z.object({
  github_token: z.string().min(1),
});

export type UpdateGithubToken = z.infer<typeof UpdateGithubTokenSchema>;

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
