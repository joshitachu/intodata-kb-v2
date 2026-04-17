import { z } from "zod";

// --- Sources ---

export const DocumentSourceSchema = z.object({
  document_id: z.number(),
  filename: z.string(),
  score: z.number(),
  text: z.string(),
  reference_number: z.number(),
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
