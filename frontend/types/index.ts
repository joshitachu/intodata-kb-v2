import { z } from "zod";

// --- Query ---

export const QueryRequestSchema = z.object({
  query: z.string().min(3),
  top_k: z.number().int().min(1).max(50).optional().default(3),
  threshold: z.number().min(0).max(1).optional().default(0.4),
});

export type QueryRequest = z.input<typeof QueryRequestSchema>;

export const SourceSchema = z.object({
  document_id: z.number(),
  filename: z.string(),
  chunk_index: z.number(),
  score: z.number(),
  text: z.string(),
});

export type Source = z.infer<typeof SourceSchema>;

export const DocumentSourceSchema = z.object({
  document_id: z.number(),
  filename: z.string(),
  score: z.number(),
  text: z.string(),
  reference_number: z.number(),
});

export type DocumentSource = z.infer<typeof DocumentSourceSchema>;

export const QueryResponseSchema = z.object({
  answer: z.string(),
  sources: z.array(DocumentSourceSchema),
  latency_ms: z.number(),
});

export type QueryResponse = z.infer<typeof QueryResponseSchema>;

// --- Documents ---

export const DocumentResponseSchema = z.object({
  id: z.number(),
  filename: z.string(),
  file_type: z.string(),
  source: z.string().nullable(),
  author: z.string().nullable(),
  department: z.string().nullable(),
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

// --- Health ---

export const ServiceStatusSchema = z.object({
  status: z.string(),
  message: z.string().nullable(),
});

export const HealthResponseSchema = z.object({
  status: z.string(),
  database: ServiceStatusSchema,
  qdrant: ServiceStatusSchema,
  ollama: ServiceStatusSchema,
});

export type HealthResponse = z.infer<typeof HealthResponseSchema>;

// --- Query History (client-side) ---

export interface QueryHistoryEntry {
  id: string;
  query: string;
  answer: string;
  sources: DocumentSource[];
  latency_ms: number;
  timestamp: Date;
}

// --- Conversations ---

export const ConversationMessageRequestSchema = z.object({
  query: z.string().min(1),
  top_k: z.number().int().min(1).max(50).optional().default(10),
  threshold: z.number().min(0).max(1).optional().default(0.5),
});

export type ConversationMessageRequest = z.input<
  typeof ConversationMessageRequestSchema
>;

export const MessageResponseSchema = z.object({
  id: z.number(),
  role: z.string(),
  content: z.string(),
  sources: z.array(DocumentSourceSchema).nullable(),
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
