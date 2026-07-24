export interface AgentTraceStep {
  sequence: number;
  kind: string;
  capability_id: string | null;
  semantic_name: string | null;
  call_ref: string | null;
  parent_call_ref: string | null;
  attempt: number | null;
  phase: string | null;
  reason_code: string | null;
  task_type: string | null;
  trigger: string | null;
  recovery_from: string | null;
  arguments_hash: string | null;
  argument_shape: Record<string, unknown>;
  validation_errors: Array<{ path: string[]; code: string; message: string }>;
  details: Record<string, unknown>;
  policy_status: string | null;
  observation_status: string | null;
  stop_reason: string | null;
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  evidence_refs: string[];
  workflow_ref: Record<string, unknown> | null;
  receipt_ref: Record<string, unknown> | null;
  started_at: string;
  finished_at: string | null;
}

export interface AgentTraceSummary {
  run_id: string;
  conversation_id: number;
  owner_user_id: number;
  runtime_cohort: string;
  transport: string;
  status: string;
  stop_reason: string | null;
  objective_summary: string | null;
  model_calls: number;
  tool_calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  started_at: string;
  finished_at: string | null;
}

export interface AgentTraceDetail extends AgentTraceSummary {
  manifest_snapshot_hash: string | null;
  budget: Record<string, unknown>;
  usage: Record<string, unknown>;
  timing: Record<string, unknown>;
  steps: AgentTraceStep[];
}

export interface AgentTraceList {
  traces: AgentTraceSummary[];
  total: number;
}
