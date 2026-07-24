"use client";

import { useCallback, useEffect, useState } from "react";
import { Activity, AlertTriangle, CheckCircle2, Clock3, Cpu, RefreshCw, Route, ShieldCheck, Wrench } from "lucide-react";
import type { AgentTraceDetail, AgentTraceList, AgentTraceStep, AgentTraceSummary } from "@/types/agentTrace";

const statusTone: Record<string, string> = {
  completed: "bg-emerald-100 text-emerald-800",
  failed: "bg-red-100 text-red-800",
  interrupted: "bg-amber-100 text-amber-800",
  running: "bg-blue-100 text-blue-800",
  waiting: "bg-violet-100 text-violet-800",
  succeeded: "bg-emerald-100 text-emerald-800",
  validation_failed: "bg-red-100 text-red-800",
  call_set_rejected: "bg-amber-100 text-amber-800",
  selected: "bg-blue-100 text-blue-800",
  proposed: "bg-slate-100 text-slate-700",
  triggered: "bg-violet-100 text-violet-800",
  denied: "bg-red-100 text-red-800",
};

function Badge({ value }: { value: string | null }) {
  if (!value) return null;
  return <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${statusTone[value] ?? "bg-muted text-muted-foreground"}`}>{value.replaceAll("_", " ")}</span>;
}

export function AgentTraceExplorer() {
  const [traces, setTraces] = useState<AgentTraceSummary[]>([]);
  const [selected, setSelected] = useState<AgentTraceDetail | null>(null);
  const [status, setStatus] = useState("");
  const [tool, setTool] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    const params = new URLSearchParams({ limit: "50" });
    if (status) params.set("status", status);
    if (tool.trim()) params.set("tool", tool.trim());
    try {
      const response = await fetch(`/api/admin/agent-traces?${params}`);
      if (!response.ok) throw new Error("Could not load agent traces");
      const payload = (await response.json()) as AgentTraceList;
      setTraces(payload.traces);
      if (selected && !payload.traces.some((trace) => trace.run_id === selected.run_id)) setSelected(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load agent traces");
    } finally {
      setLoading(false);
    }
  }, [selected, status, tool]);

  useEffect(() => { void load(); }, [status]); // eslint-disable-line react-hooks/exhaustive-deps

  async function openTrace(runId: string) {
    setError("");
    try {
      const response = await fetch(`/api/admin/agent-traces/${encodeURIComponent(runId)}`);
      if (!response.ok) throw new Error("Could not load this trace");
      setSelected((await response.json()) as AgentTraceDetail);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load this trace");
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-background">
      <header className="border-b px-6 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div><h1 className="text-xl font-semibold">Agent Traces</h1><p className="text-sm text-muted-foreground">Safe runtime decisions, tool attempts, validation failures, and budget outcomes.</p></div>
          <button onClick={() => void load()} className="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm hover:bg-muted"><RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />Refresh</button>
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <select aria-label="Status" value={status} onChange={(event) => setStatus(event.target.value)} className="rounded-md border bg-background px-3 py-2 text-sm">
            <option value="">All statuses</option><option value="completed">Completed</option><option value="failed">Failed</option><option value="interrupted">Interrupted</option><option value="running">Running</option>
          </select>
          <input aria-label="Tool" value={tool} onChange={(event) => setTool(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void load(); }} placeholder="Capability or tool name" className="w-64 rounded-md border bg-background px-3 py-2 text-sm" />
          <button onClick={() => void load()} className="rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground">Apply</button>
        </div>
        {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[360px_1fr]">
        <aside className="overflow-y-auto border-r">
          {!loading && traces.length === 0 && <p className="p-6 text-sm text-muted-foreground">No traces match these filters.</p>}
          {traces.map((trace) => (
            <button key={trace.run_id} onClick={() => void openTrace(trace.run_id)} className={`w-full border-b p-4 text-left hover:bg-muted/60 ${selected?.run_id === trace.run_id ? "bg-muted" : ""}`}>
              <div className="flex items-center justify-between gap-2"><code className="truncate text-xs">{trace.run_id}</code><Badge value={trace.status} /></div>
              <p className="mt-2 text-sm font-medium">Conversation {trace.conversation_id}</p>
              <div className="mt-2 flex items-center gap-3 text-xs text-muted-foreground"><span>{trace.runtime_cohort}</span><span>{trace.tool_calls} tools</span><span>{trace.model_calls} model</span></div>
              <p className="mt-2 text-xs text-muted-foreground">{new Date(trace.started_at).toLocaleString()}</p>
            </button>
          ))}
        </aside>

        <main className="overflow-y-auto p-6">
          {!selected ? <div className="grid h-full place-items-center text-center text-muted-foreground"><div><Activity className="mx-auto mb-3 h-8 w-8" /><p>Select a run to inspect its timeline.</p></div></div> : <TraceDetail trace={selected} />}
        </main>
      </div>
    </div>
  );
}

function TraceDetail({ trace }: { trace: AgentTraceDetail }) {
  const routing = trace.steps.find((step) => step.kind === "routing");
  const technical = trace.steps.filter((step) => step.kind === "runtime");
  const workflows = trace.steps.filter((step) => step.kind === "workflow" || step.kind === "plan");
  const final = trace.steps.findLast((step) => step.kind === "final");
  const tools = groupToolSteps(trace.steps.filter((step) => step.kind === "tool"));
  const successfulTools = tools.filter((tool) => tool.status === "succeeded" || tool.status === "triggered").length;
  const failedTools = tools.filter((tool) => !["succeeded", "triggered", "proposed"].includes(tool.status)).length;
  const outcomeText = trace.status === "completed"
    ? `The agent answered successfully. ${successfulTools} tool call${successfulTools === 1 ? "" : "s"} completed${failedTools ? ` and ${failedTools} failed` : " without a recorded failure"}.`
    : `The run ${trace.status}. ${trace.stop_reason ? `It stopped because ${humanize(trace.stop_reason)}.` : "No terminal reason was recorded."}`;

  return <div className="mx-auto max-w-5xl">
    <div className="flex flex-wrap items-start justify-between gap-3"><div><div className="flex items-center gap-2"><h2 className="text-lg font-semibold">Conversation {trace.conversation_id}</h2><Badge value={trace.status} /></div><code className="text-xs text-muted-foreground">{trace.run_id}</code></div><div className="text-right text-sm"><p>${trace.cost_usd.toFixed(5)}</p><p className="text-muted-foreground">{trace.input_tokens.toLocaleString()} in · {trace.output_tokens.toLocaleString()} out</p></div></div>
    <div className={`mt-5 rounded-lg border p-4 ${trace.status === "completed" ? "border-emerald-200 bg-emerald-50" : "border-amber-200 bg-amber-50"}`}>
      <div className="flex items-start gap-3">{trace.status === "completed" ? <CheckCircle2 className="mt-0.5 h-5 w-5 text-emerald-700" /> : <AlertTriangle className="mt-0.5 h-5 w-5 text-amber-700" />}<div><p className="font-medium">Run outcome</p><p className="mt-1 text-sm text-muted-foreground">{outcomeText}</p></div></div>
    </div>
    <div className="mt-5 grid gap-3 sm:grid-cols-5">{[["Cohort", trace.runtime_cohort], ["Model", String(trace.timing.model ?? "—")], ["Transport", trace.transport], ["Stop reason", trace.stop_reason ?? "—"], ["Calls", `${trace.model_calls} model / ${trace.tool_calls} tool`]].map(([label, value]) => <div key={label} className="rounded-lg border p-3"><p className="text-xs text-muted-foreground">{label}</p><p className="mt-1 break-words text-sm font-medium">{value}</p></div>)}</div>
    <h3 className="mt-8 font-semibold">What happened</h3>
    <p className="mt-1 text-sm text-muted-foreground">This view shows decisions and outcomes. Low-level streaming phases are separated below.</p>
    <div className="mt-3 space-y-3">
      {routing && <RoutingCard step={routing} />}
      {tools.map((tool) => <ToolCard key={tool.key} tool={tool} />)}
      {workflows.map((step) => <WorkflowCard key={step.sequence} step={step} />)}
      {final && <FinalCard step={final} />}
    </div>
    {technical.length > 0 && <details className="mt-6 rounded-lg border bg-muted/20">
      <summary className="cursor-pointer px-4 py-3 text-sm font-medium">Technical phases ({technical.length})</summary>
      <div className="border-t px-4 py-3 text-sm text-muted-foreground">{technical.map((step) => <div key={step.sequence} className="flex items-center justify-between border-b py-2 last:border-0"><span><code>#{step.sequence}</code> · {step.phase === "model" ? "Model processing completed" : step.phase === "tool" ? "Tool orchestration completed" : "Runtime processing completed"}</span><Badge value={step.observation_status} /></div>)}</div>
    </details>}
    {trace.steps.length === 0 && <div className="mt-3 rounded-lg border p-6 text-sm text-muted-foreground"><Clock3 className="mb-2 h-5 w-5" />No diagnostic steps were recorded for this historical run.</div>}
  </div>;
}

interface CapabilityContract {
  label?: string;
  purpose?: string;
  provider_id?: string;
  provider_type?: string;
  risk_tier?: string;
  action?: string;
  preconditions?: string[];
  selection_guidance?: string[];
  required_permissions?: string[];
  input_schema?: { fields?: Array<Record<string, unknown>>; additional_properties?: boolean };
}

interface ToolLifecycle {
  key: string;
  proposed: AgentTraceStep;
  completed?: AgentTraceStep;
  status: string;
}

function groupToolSteps(steps: AgentTraceStep[]): ToolLifecycle[] {
  const groups = new Map<string, ToolLifecycle>();
  for (const step of steps) {
    const key = step.call_ref || `${step.semantic_name ?? "tool"}:${step.attempt ?? 1}:${step.sequence}`;
    const existing = groups.get(key);
    if (!existing) {
      groups.set(key, {
        key,
        proposed: step,
        completed: step.observation_status !== "proposed" ? step : undefined,
        status: step.observation_status ?? step.policy_status ?? "recorded",
      });
      continue;
    }
    if (step.observation_status !== "proposed") existing.completed = step;
    existing.status = step.observation_status ?? step.policy_status ?? existing.status;
  }
  return [...groups.values()].sort((left, right) => left.proposed.sequence - right.proposed.sequence);
}

function RoutingCard({ step }: { step: AgentTraceStep }) {
  const routing = (step.details.routing ?? {}) as Record<string, unknown>;
  return <article className="rounded-lg border p-4">
    <div className="flex items-start justify-between gap-3"><div className="flex gap-3"><Route className="mt-0.5 h-5 w-5 text-blue-600" /><div><p className="font-medium">{String(routing.label ?? "Runtime route selected")} <span className="text-xs font-normal text-muted-foreground">#{step.sequence}</span></p><p className="mt-1 text-sm text-muted-foreground">{String(routing.explanation ?? "The server selected the runtime policy for this turn.")}</p></div></div><Badge value="selected" /></div>
    <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2"><div><dt className="text-muted-foreground">Decision made by</dt><dd className="font-medium">{String(routing.decision_authority ?? "server policy")}</dd></div><div><dt className="text-muted-foreground">Transport</dt><dd className="font-medium">{String(routing.transport ?? "—")}</dd></div></dl>
  </article>;
}

function ToolCard({ tool }: { tool: ToolLifecycle }) {
  const result = tool.completed ?? tool.proposed;
  const capability = (result.details.capability ?? tool.proposed.details.capability ?? {}) as CapabilityContract;
  const schemaFields = capability.input_schema?.fields ?? [];
  const observedShape = Object.keys(tool.proposed.argument_shape).length > 0 ? tool.proposed.argument_shape : result.argument_shape;
  const summary = result.details.summary ? String(result.details.summary) : null;
  const isFailure = !["succeeded", "triggered", "proposed"].includes(tool.status);
  const title = capability.label || result.details.tool_label?.toString() || result.semantic_name || "Tool call";
  return <article className={`rounded-lg border p-4 ${isFailure ? "border-red-200" : ""}`}>
    <div className="flex flex-wrap items-start justify-between gap-3"><div className="flex gap-3"><Wrench className="mt-0.5 h-5 w-5 text-blue-600" /><div><p className="font-medium">{title} <span className="text-xs font-normal text-muted-foreground">#{tool.proposed.sequence}{tool.completed ? ` → #${tool.completed.sequence}` : ""} · attempt {tool.proposed.attempt ?? 1}</span></p><p className="mt-1 text-sm text-muted-foreground">{capability.purpose || "The model selected this capability for the turn."}</p></div></div><Badge value={tool.status} /></div>
    <div className="mt-3 grid gap-2 rounded-md bg-muted/40 p-3 text-xs sm:grid-cols-3"><div><p className="text-muted-foreground">Capability</p><code>{result.capability_id ?? "unknown"}</code></div><div><p className="text-muted-foreground">Provider</p><p className="font-medium">{capability.provider_id ?? "unknown"}</p></div><div><p className="text-muted-foreground">Risk</p><p className="font-medium">{humanize(capability.risk_tier ?? "unknown")}</p></div></div>
    <div className="mt-3 text-sm"><p><span className="font-medium">Why selected:</span> {explainReason(tool.proposed.reason_code, capability)}</p>{summary && <p className="mt-1"><span className="font-medium">Outcome:</span> {summary}</p>}</div>
    <details className="mt-3 rounded-md border"><summary className="cursor-pointer px-3 py-2 text-sm font-medium">Input contract and observed shape</summary><div className="border-t p-3 text-sm">
      <p className="font-medium">Expected schema</p>{schemaFields.length > 0 ? <div className="mt-2 overflow-x-auto"><table className="w-full text-left text-xs"><thead><tr className="border-b"><th className="py-1 pr-3">Field</th><th className="py-1 pr-3">Type</th><th className="py-1">Required</th></tr></thead><tbody>{schemaFields.map((field) => <tr key={String(field.name)} className="border-b last:border-0"><td className="py-1 pr-3"><code>{String(field.name)}</code></td><td className="py-1 pr-3">{Array.isArray(field.type) ? field.type.join(" | ") : String(field.type)}</td><td className="py-1">{field.required ? "yes" : "no"}</td></tr>)}</tbody></table></div> : <p className="mt-1 text-muted-foreground">This capability declares no input fields.</p>}
      <p className="mt-3 font-medium">Observed argument structure</p>{Object.keys(observedShape).length > 0 ? <pre className="mt-2 overflow-x-auto rounded bg-muted p-3 text-xs">{JSON.stringify(observedShape, null, 2)}</pre> : <p className="mt-1 text-muted-foreground">Not captured for this call. Argument values are never retained.</p>}
    </div></details>
    {result.validation_errors.length > 0 && <div className="mt-3 rounded-md border border-red-200 bg-red-50 p-3"><p className="text-sm font-medium text-red-800">Validation diagnostics</p>{result.validation_errors.map((item, index) => <p key={`${item.code}-${index}`} className="mt-1 text-xs text-red-700"><code>{item.path.join(".") || "$"}</code> · {item.code} · {item.message}</p>)}</div>}
    <div className="mt-3 flex flex-wrap gap-3 text-xs text-muted-foreground">{result.evidence_refs.length > 0 && <span>{result.evidence_refs.length} evidence reference(s)</span>}{result.workflow_ref && <span>Workflow reference captured</span>}{result.receipt_ref && <span>Effect receipt captured</span>}{result.call_ref && <span>Call <code>{result.call_ref}</code></span>}</div>
  </article>;
}

function WorkflowCard({ step }: { step: AgentTraceStep }) {
  const summary = step.details.summary ? String(step.details.summary) : "The capability linked this turn to a durable workflow.";
  return <article className="rounded-lg border p-4"><div className="flex items-start justify-between gap-3"><div className="flex gap-3"><ShieldCheck className="mt-0.5 h-5 w-5 text-violet-600" /><div><p className="font-medium">{step.kind === "plan" ? "Durable plan updated" : "Workflow linked"} <span className="text-xs font-normal text-muted-foreground">#{step.sequence}</span></p><p className="mt-1 text-sm text-muted-foreground">{summary}</p>{step.details.workflow_run_id != null && <p className="mt-2 text-xs text-muted-foreground">Workflow <code>{String(step.details.workflow_run_id)}</code></p>}</div></div><Badge value={step.observation_status} /></div></article>;
}

function FinalCard({ step }: { step: AgentTraceStep }) {
  const success = step.observation_status === "completed";
  return <article className="rounded-lg border p-4"><div className="flex items-start justify-between gap-3"><div className="flex gap-3"><Cpu className="mt-0.5 h-5 w-5 text-emerald-600" /><div><p className="font-medium">Final response {success ? "delivered" : "stopped"} <span className="text-xs font-normal text-muted-foreground">#{step.sequence}</span></p><p className="mt-1 text-sm text-muted-foreground">The runtime recorded the terminal outcome after model synthesis and policy checks.</p></div></div><Badge value={step.observation_status} /></div></article>;
}

function explainReason(reason: string | null, capability: CapabilityContract): string {
  if (reason === "correct_validation_failure") return "The model corrected a previous schema validation failure and retried once.";
  if (reason === "model_selected_capability") return capability.selection_guidance?.[0] || "The model matched the request to this capability's declared purpose.";
  return reason ? humanize(reason) : "No structured selection reason was recorded.";
}

function humanize(value: string): string {
  return value.replaceAll("_", " ");
}
