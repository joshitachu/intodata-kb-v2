import { z } from "zod";

// Existing job-detail contract (predates S1/S2), now formalized as a Zod
// schema so `useJobDetail` and `ComponentInspector` both validate against
// it instead of trusting `unknown` JSON.
// GET /api/projects/{id}/jobs/{jobName}/detail -> JobDetailResponse
//
// `scan_data` is the raw per-job scan JSON; the backend types it loosely
// (`dict`) because it is the scanner's own cache format. This schema only
// names the fields the UI actually consumes - extra scan keys are dropped
// by zod's default object parsing, never rendered or logged.

export const ComponentParamSchema = z.object({
  name: z.string(),
  value: z.string(),
  field_type: z.string().optional().default(""),
});
export type ComponentParam = z.infer<typeof ComponentParamSchema>;

// Mirrors intodoc's Component model
// (knowledge_base/intodoc/src/intodoc/models.py). The scanner's credential
// filter runs before this JSON is ever persisted to the scan cache - this
// type renders whatever already passed that filter and must never apply
// its own redaction logic (that would duplicate, not replace, the
// scanner's responsibility).
export const ScanComponentSchema = z.object({
  unique_name: z.string(),
  component_type: z.string(),
  params: z.array(ComponentParamSchema).optional().default([]),
  code: z.string().nullable().optional().default(null),
  incoming_connections: z.array(z.string()).optional().default([]),
  outgoing_connections: z.array(z.string()).optional().default([]),
  metadata_connection_ref: z.string().optional().default(""),
});
export type ScanComponent = z.infer<typeof ScanComponentSchema>;

export const JobScanDataSchema = z.object({
  name: z.string().optional(),
  path: z.string().optional(),
  purpose: z.string().optional(),
  author: z.string().optional(),
  version: z.string().optional(),
  creation_date: z.string().optional(),
  modification_date: z.string().optional(),
  artifact_kind: z.string().optional(),
  artifact_role: z.string().optional(),
  components: z.array(ScanComponentSchema).optional().default([]),
});
export type JobScanData = z.infer<typeof JobScanDataSchema>;

export const JobDocSectionSchema = z.object({
  heading: z.string(),
  content: z.string(),
});
export type JobDocSection = z.infer<typeof JobDocSectionSchema>;

export const JobDetailResponseSchema = z.object({
  project_id: z.string(),
  job_name: z.string(),
  group: z.string(),
  scan_data: JobScanDataSchema.optional().default({}),
  doc_sections: z.array(JobDocSectionSchema).optional().default([]),
  summary: z.string().optional().default(""),
  direction: z.string().optional().default(""),
});
export type JobDetailResponse = z.infer<typeof JobDetailResponseSchema>;
