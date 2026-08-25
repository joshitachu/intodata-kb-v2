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

/**
 * Business topology is an additive, agent-produced interpretation of the
 * existing documentation and bounded scanner evidence.  The technical
 * landscape above remains the source of truth for graph edges and lineage.
 */
const BusinessEvidenceIdsSchema = z.array(z.string()).default([]);
const ClaimStatusSchema = z.enum(["observed", "inferred", "proposed", "confirmed", "not_established", "rejected"]);

export const BusinessSystemSchema = z.object({
  id: z.string(),
  name: z.string(),
  aliases: z.array(z.string()).default([]),
  kind: z.enum(["software_system", "data_system", "external_service", "organization", "person_role", "unresolved"]).default("software_system"),
  description: z.string().default(""),
  // Business meaning is optional in the scanner contract, but the backend
  // always serializes these fields (with an empty value when it is not
  // established). Keep them in the browser contract so a valid published
  // topology is not rejected by the strict parser.
  purpose: z.string().optional(),
  owner: z.string().optional(),
  status: ClaimStatusSchema,
  confidence: z.number().min(0).max(1),
  evidence_ids: BusinessEvidenceIdsSchema,
}).strict();

export const BusinessIntegrationSolutionSchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string().default(""),
  platform: z.string().default(""),
  role: z.string().default("execution"),
  status: ClaimStatusSchema,
  confidence: z.number().min(0).max(1),
  artifact_ids: z.array(z.string()).default([]),
  evidence_ids: BusinessEvidenceIdsSchema,
}).strict();

export const BusinessObjectSchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string().default(""),
  status: ClaimStatusSchema,
  confidence: z.number().min(0).max(1),
  aliases: z.array(z.string()).default([]),
  evidence_ids: BusinessEvidenceIdsSchema,
}).strict();

export const BusinessDataStreamSchema = z.object({
  id: z.string(),
  source_system_id: z.string(),
  destination_system_id: z.string(),
  name: z.string(),
  description: z.string().default(""),
  business_object_ids: z.array(z.string()).default([]),
  business_summary: z.string().default(""),
  integration_solution_ids: z.array(z.string()).default([]),
  protocols: z.array(z.string()).default([]),
  requiredness: z.enum(["required", "optional", "conditional", "not_established"]).default("not_established"),
  delivery_path_id: z.string().nullable().optional(),
  status: ClaimStatusSchema,
  confidence: z.number().min(0).max(1),
  control_ids: z.array(z.string()).default([]),
  risk_ids: z.array(z.string()).default([]),
  unknown_ids: z.array(z.string()).default([]),
  runtime_observation_ids: z.array(z.string()).optional(),
  conflict_ids: z.array(z.string()).optional(),
  evidence_ids: BusinessEvidenceIdsSchema,
  artifact_ids: z.array(z.string()).default([]),
}).strict();

export const BusinessDeliveryPathSegmentSchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string(),
  sequence: z.number().int().positive(),
  source_ref: z.string(),
  destination_ref: z.string(),
  mechanism: z.string().default("unknown"),
  protocol: z.string().default("unknown"),
  resource: z.string().default(""),
  component: z.string().default(""),
  artifact_ids: z.array(z.string()).default([]),
  mode: z.enum(["direct", "buffered", "batch", "scheduled", "event_driven", "file_transfer", "database", "unknown"]).default("unknown"),
  status: ClaimStatusSchema,
  confidence: z.number().min(0).max(1),
  evidence_ids: BusinessEvidenceIdsSchema,
}).strict();

export const BusinessDeliveryPathSchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string().default(""),
  stream_id: z.string(),
  segments: z.array(BusinessDeliveryPathSegmentSchema).default([]),
  status: ClaimStatusSchema,
  confidence: z.number().min(0).max(1),
  evidence_ids: BusinessEvidenceIdsSchema,
}).strict();

export const TransformationMappingSchema = z.object({
  source: z.string(),
  target: z.string(),
  mapping_type: z.enum(["direct", "value", "derived", "constant", "unknown"]).default("unknown"),
}).strict();

const EvidenceBackedClaimSchema = z.object({
  id: z.string(),
  name: z.string().default(""),
  description: z.string().default(""),
  status: ClaimStatusSchema,
  confidence: z.number().min(0).max(1),
  evidence_ids: BusinessEvidenceIdsSchema,
}).strict();

export const BusinessTransformationSchema = EvidenceBackedClaimSchema.extend({
  stream_ids: z.array(z.string()).default([]),
  integration_solution_ids: z.array(z.string()).default([]),
  artifact_ids: z.array(z.string()).default([]),
  mappings: z.array(TransformationMappingSchema).default([]),
}).strict();

export const RuntimeObservationSchema = EvidenceBackedClaimSchema.extend({
  stream_ids: z.array(z.string()).default([]),
  delivery_path_ids: z.array(z.string()).default([]),
  observed_behavior: z.string(),
  observation_window: z.string().default(""),
}).strict();

export const BusinessConflictSchema = EvidenceBackedClaimSchema.extend({
  conflict_type: z.enum([
    "supporting_vs_implementation",
    "runtime_vs_implementation",
    "supporting_sources_disagree",
    "unresolved",
  ]),
  subject_refs: z.array(z.string()).default([]),
  implementation_evidence_ids: z.array(z.string()).default([]),
  supporting_evidence_ids: z.array(z.string()).default([]),
  resolution: z.string().default(""),
}).strict();

export const BusinessControlSchema = EvidenceBackedClaimSchema.extend({
  stream_ids: z.array(z.string()).default([]),
  control_type: z.string().default(""),
}).strict();

export const BusinessRiskSchema = EvidenceBackedClaimSchema.extend({
  stream_ids: z.array(z.string()).default([]),
  severity: z.enum(["low", "medium", "high", "critical", "unknown"]).default("unknown"),
}).strict();

export const BusinessUnknownSchema = EvidenceBackedClaimSchema.extend({
  stream_ids: z.array(z.string()).default([]),
  question: z.string(),
}).strict();

export const BusinessAnnotationSchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string(),
  status: ClaimStatusSchema,
  confidence: z.number().min(0).max(1),
  evidence_ids: BusinessEvidenceIdsSchema,
}).strict();

export const ArtifactAnnotationSchema = BusinessAnnotationSchema.extend({
  artifact_id: z.string(),
  business_role: z.string(),
}).strict();

export const ComponentAnnotationSchema = BusinessAnnotationSchema.extend({
  component_id: z.string(),
  human_explanation: z.string(),
}).strict();

export const LineageAnnotationSchema = BusinessAnnotationSchema.extend({
  lineage_id: z.string(),
  business_meaning: z.string(),
  business_object_id: z.string().nullable().optional(),
}).strict();

export const BusinessTopologySchema = z.object({
  schema_version: z.string(),
  scan_fingerprint: z.string(),
  metadata: z.object({
    project_id: z.string(),
    scan_fingerprint: z.string(),
    generated_by: z.string(),
    model_name: z.string().default(""),
  }).strict(),
  systems: z.array(BusinessSystemSchema).default([]),
  integration_solutions: z.array(BusinessIntegrationSolutionSchema).default([]),
  business_objects: z.array(BusinessObjectSchema).default([]),
  data_streams: z.array(BusinessDataStreamSchema).default([]),
  delivery_paths: z.array(BusinessDeliveryPathSchema).default([]),
  transformations: z.array(BusinessTransformationSchema).default([]),
  controls: z.array(BusinessControlSchema).default([]),
  risks: z.array(BusinessRiskSchema).default([]),
  runtime_observations: z.array(RuntimeObservationSchema).optional(),
  conflicts: z.array(BusinessConflictSchema).optional(),
  unknowns: z.array(BusinessUnknownSchema).default([]),
  artifact_annotations: z.array(ArtifactAnnotationSchema).default([]),
  component_annotations: z.array(ComponentAnnotationSchema).default([]),
  lineage_annotations: z.array(LineageAnnotationSchema).default([]),
  coverage: z.object({
    total_artifacts: z.number().int().nonnegative(),
    cited_artifacts: z.number().int().nonnegative(),
    total_evidence: z.number().int().nonnegative(),
    cited_evidence: z.number().int().nonnegative(),
    coverage_ratio: z.number().min(0).max(1),
    technical_relationships: z.number().int().nonnegative().optional(),
    grounded_technical_relationships: z.number().int().nonnegative().optional(),
    notes: z.array(z.string()).default([]),
  }).strict(),
}).strict();

export const TopologyStateSchema = z.object({
  available: z.boolean().default(false),
  validated: z.boolean().default(false),
  stale: z.boolean().default(false),
  source: z.string().default("none"),
  message: z.string().optional(),
}).strict();

/** Identity-only bridge from business claims back to scanner entities. */
export const TopologyEvidenceArtifactSchema = z.object({
  id: z.string(),
  name: z.string(),
  job_id: z.string(),
  source_root: z.string(),
  artifact_kind: z.string(),
  artifact_role: z.string(),
}).strict();

export const TopologyEvidenceComponentSchema = z.object({
  id: z.string(),
  artifact_id: z.string(),
  name: z.string(),
  component_type: z.string(),
}).strict();

export const TopologyEvidenceLineageSchema = z.object({
  id: z.string(),
  job_name: z.string(),
  source_table: z.string(),
  source_column: z.string(),
  source_component: z.string(),
  target_table: z.string(),
  target_column: z.string(),
  target_component: z.string(),
}).strict();

export const TopologyEvidenceRelationSchema = z.object({
  id: z.string(),
  source: z.string(),
  target: z.string(),
  relation_type: z.string(),
  component_name: z.string().default(""),
  resolution: z.string().default("unknown"),
}).strict();

export const TopologyEvidenceSchema = z.object({
  artifacts: z.array(TopologyEvidenceArtifactSchema).default([]),
  components: z.array(TopologyEvidenceComponentSchema).default([]),
  lineage: z.array(TopologyEvidenceLineageSchema).default([]),
  relations: z.array(TopologyEvidenceRelationSchema).default([]),
}).strict();

export const ProjectLandscapeResponseSchema = z.object({
  systems: z.array(LandscapeSystemSchema).default([]),
  applications: z.array(LandscapeApplicationSchema).default([]),
  edges: z.array(LandscapeEdgeSchema).default([]),
  channels: z.array(LandscapeChannelSchema).default([]),
  interfaces: z.array(LandscapeInterfaceSchema).default([]),
  diagnostics: z.array(LandscapeDiagnosticSchema).default([]),
  // The backend deliberately returns null when no current validated business
  // overlay exists; the technical scanner landscape must remain renderable.
  business_topology: BusinessTopologySchema.nullable().optional(),
  topology_state: TopologyStateSchema.optional(),
  topology_evidence: TopologyEvidenceSchema.optional(),
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
export type BusinessSystem = z.infer<typeof BusinessSystemSchema>;
export type BusinessIntegrationSolution = z.infer<typeof BusinessIntegrationSolutionSchema>;
export type BusinessObject = z.infer<typeof BusinessObjectSchema>;
export type BusinessDataStream = z.infer<typeof BusinessDataStreamSchema>;
export type BusinessDeliveryPath = z.infer<typeof BusinessDeliveryPathSchema>;
export type BusinessDeliveryPathSegment = z.infer<typeof BusinessDeliveryPathSegmentSchema>;
export type BusinessTransformation = z.infer<typeof BusinessTransformationSchema>;
export type TransformationMapping = z.infer<typeof TransformationMappingSchema>;
export type BusinessControl = z.infer<typeof BusinessControlSchema>;
export type BusinessRisk = z.infer<typeof BusinessRiskSchema>;
export type RuntimeObservation = z.infer<typeof RuntimeObservationSchema>;
export type BusinessConflict = z.infer<typeof BusinessConflictSchema>;
export type BusinessUnknown = z.infer<typeof BusinessUnknownSchema>;
export type BusinessAnnotation = z.infer<typeof BusinessAnnotationSchema>;
export type ArtifactAnnotation = z.infer<typeof ArtifactAnnotationSchema>;
export type ComponentAnnotation = z.infer<typeof ComponentAnnotationSchema>;
export type LineageAnnotation = z.infer<typeof LineageAnnotationSchema>;
export type BusinessTopology = z.infer<typeof BusinessTopologySchema>;
export type TopologyState = z.infer<typeof TopologyStateSchema>;
export type TopologyEvidence = z.infer<typeof TopologyEvidenceSchema>;
export type TopologyEvidenceArtifact = z.infer<typeof TopologyEvidenceArtifactSchema>;
export type TopologyEvidenceComponent = z.infer<typeof TopologyEvidenceComponentSchema>;
export type TopologyEvidenceLineage = z.infer<typeof TopologyEvidenceLineageSchema>;
export type TopologyEvidenceRelation = z.infer<typeof TopologyEvidenceRelationSchema>;
export type ProjectLandscapeResponse = z.infer<typeof ProjectLandscapeResponseSchema>;
