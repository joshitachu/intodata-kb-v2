import { z } from "zod";

/**
 * Scanner-computed L1/L2 landscape contract.  These schemas deliberately
 * mirror the MCP response instead of accepting arbitrary JSON: the browser
 * is a renderer of evidence, never an extra topology inference engine.
 */
export const LandscapeRedactionDescriptorSchema = z.object({
  scheme: z.string().nullable().optional(),
  category: z.string().nullable().optional(),
  auth_present: z.boolean().nullable().optional(),
  auth_type: z.enum(["none", "basic", "bearer", "api_key", "oauth", "unknown"]).nullable().optional(),
  tls: z.boolean().nullable().optional(),
  host_present: z.boolean().nullable().optional(),
}).strict();

export const LandscapeSystemEvidenceSchema = z.object({
  signal_type: z.string(),
  label: z.string(),
  detail: z.record(z.unknown()).default({}),
}).strict();

export const LandscapeSystemSchema = z.object({
  id: z.string(),
  name: z.string(),
  kind: z.string().default("unknown"),
  evidence: z.array(LandscapeSystemEvidenceSchema).default([]),
  source_roots: z.array(z.string()).default([]),
}).strict();

export const LandscapeApplicationSchema = z.object({
  id: z.string(),
  source_root: z.string(),
  source_roots: z.array(z.string()).default([]),
  project_id: z.string().default(""),
  label: z.string().default(""),
  description: z.string().default(""),
  role: z.string().default("execution"),
  artifact_count: z.number().int().nonnegative().default(0),
  kinds_breakdown: z.record(z.number().int().nonnegative()).default({}),
}).strict();

export const LandscapeProvenanceSchema = z.object({
  artifact_id: z.string(),
  qualified_artifact_id: z.string(),
  source_root: z.string(),
  component_name: z.string().default(""),
  fact_type: z.string(),
}).strict();

export const LandscapeEdgeSchema = z.object({
  source: z.string(),
  target: z.string(),
  source_kind: z.string(),
  target_kind: z.string(),
  dependency_type: z.string(),
  mechanism: z.string().default("unknown"),
  direction: z.string().default("unknown"),
  reliability: z.string().default("unknown"),
  confidence: z.string().default("unknown"),
  evidence_count: z.number().int().nonnegative().default(0),
  provenance: z.array(LandscapeProvenanceSchema).default([]),
}).strict();

const LandscapeArtifactEvidenceSchema = z.object({
  direction: z.string(),
  artifact_id: z.string(),
  qualified_artifact_id: z.string(),
  source_root: z.string(),
  component_name: z.string().default(""),
  fact_type: z.string(),
  redaction_descriptor: LandscapeRedactionDescriptorSchema.nullable().optional(),
}).strict();

export const LandscapeChannelSchema = LandscapeArtifactEvidenceSchema.extend({
  channel_type: z.string(),
  channel: z.string(),
}).strict();

export const LandscapeInterfaceSchema = LandscapeArtifactEvidenceSchema.extend({
  endpoint_type: z.string(),
}).strict();

export const LandscapeDiagnosticSchema = z.object({
  kind: z.string(),
  description: z.string(),
  evidence: z.array(z.string()).default([]),
  severity: z.string().default("info"),
}).strict();

export const SemanticTopologyEvidenceSchema = z.object({
  signal_type: z.string(),
  label: z.string(),
  artifact_id: z.string().default(""),
  qualified_artifact_id: z.string().default(""),
  source_root: z.string().default(""),
  component_name: z.string().default(""),
}).strict();

export const SemanticSystemSchema = z.object({
  id: z.string(),
  name: z.string(),
  kind: z.string().default("software_system"),
  status: z.string().default("proposed"),
  confidence: z.string().default("unknown"),
  aliases: z.array(z.string()).default([]),
  evidence: z.array(SemanticTopologyEvidenceSchema).default([]),
}).strict();

export const IntegrationSolutionSchema = z.object({
  id: z.string(),
  name: z.string(),
  platform: z.string().default(""),
  role: z.string().default("execution"),
  source_roots: z.array(z.string()).default([]),
  artifact_count: z.number().int().nonnegative().default(0),
}).strict();

export const SemanticExchangeSchema = z.object({
  id: z.string(),
  source_system_id: z.string(),
  target_system_id: z.string(),
  label: z.string(),
  payloads: z.array(z.string()).default([]),
  mechanisms: z.array(z.string()).default([]),
  reliability: z.string().default("unknown"),
  status: z.string().default("proposed"),
  confidence: z.string().default("unknown"),
  implementation_solution_ids: z.array(z.string()).default([]),
  artifact_ids: z.array(z.string()).default([]),
  evidence: z.array(SemanticTopologyEvidenceSchema).default([]),
}).strict();

export const SemanticTopologySchema = z.object({
  systems: z.array(SemanticSystemSchema).default([]),
  integration_solutions: z.array(IntegrationSolutionSchema).default([]),
  exchanges: z.array(SemanticExchangeSchema).default([]),
  unresolved: z.array(z.record(z.unknown())).default([]),
  summary: z.record(z.unknown()).default({}),
}).strict();

export const ProjectLandscapeResponseSchema = z.object({
  systems: z.array(LandscapeSystemSchema).default([]),
  applications: z.array(LandscapeApplicationSchema).default([]),
  edges: z.array(LandscapeEdgeSchema).default([]),
  channels: z.array(LandscapeChannelSchema).default([]),
  interfaces: z.array(LandscapeInterfaceSchema).default([]),
  diagnostics: z.array(LandscapeDiagnosticSchema).default([]),
  semantic_topology: SemanticTopologySchema.optional(),
  unmapped: z.array(z.record(z.unknown())).default([]),
  summary: z.record(z.unknown()).default({}),
}).strict();

export type LandscapeRedactionDescriptor = z.infer<typeof LandscapeRedactionDescriptorSchema>;
export type LandscapeSystemEvidence = z.infer<typeof LandscapeSystemEvidenceSchema>;
export type LandscapeSystem = z.infer<typeof LandscapeSystemSchema>;
export type LandscapeApplication = z.infer<typeof LandscapeApplicationSchema>;
export type LandscapeProvenance = z.infer<typeof LandscapeProvenanceSchema>;
export type LandscapeEdge = z.infer<typeof LandscapeEdgeSchema>;
export type LandscapeChannel = z.infer<typeof LandscapeChannelSchema>;
export type LandscapeInterface = z.infer<typeof LandscapeInterfaceSchema>;
export type LandscapeDiagnostic = z.infer<typeof LandscapeDiagnosticSchema>;
export type SemanticTopologyEvidence = z.infer<typeof SemanticTopologyEvidenceSchema>;
export type SemanticSystem = z.infer<typeof SemanticSystemSchema>;
export type IntegrationSolution = z.infer<typeof IntegrationSolutionSchema>;
export type SemanticExchange = z.infer<typeof SemanticExchangeSchema>;
export type SemanticTopology = z.infer<typeof SemanticTopologySchema>;
export type ProjectLandscapeResponse = z.infer<typeof ProjectLandscapeResponseSchema>;
