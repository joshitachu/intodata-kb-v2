"use client";

import { Fragment, memo, useEffect, useMemo, useState, type ReactNode, useCallback } from "react";
import { ArrowLeft, Box, ChevronRight, Code2, FileWarning, Network, X } from "lucide-react";
import { Background, BackgroundVariant, Controls, Handle, MiniMap, Position, ReactFlow, type NodeMouseHandler, type ReactFlowInstance, useEdgesState, useNodesState } from "@xyflow/react";
import { implementingDesignDetail, integrationDetailForEdge, type ImplementingDesignDetail, type ImplementingDesignMapping } from "@/lib/integrationDetailTransform";
import {
  BUSINESS_RULE_KIND_LABEL,
  CONFLICT_TYPE_LABEL,
  DELIVERY_MODE_LABEL,
  REACHES_TARGET_LABEL,
  RISK_SEVERITY_LABEL,
  RUNTIME_OBSERVATION_KIND_LABEL,
  STREAM_CONDITION_KIND_LABEL,
  streamDeliveryPath,
  streamStructuredFacts,
  type ReachesTarget,
  type StreamFieldRow,
  type StreamStructuredFacts,
} from "@/lib/architectureViewModel";
import type { LandscapeProvenance, ProjectLandscapeResponse, StreamCondition } from "@/types/landscape";
import { connectedEdges, landscapeEntityLabel, resolveRelationshipEdges, topologyTerm, type TopologyEdge } from "@/lib/landscapeTransform";
import type { AssistantCanvasEmphasis, AssistantFocusRequest, CopilotViewContext } from "@/types/copilot";
import { assistantRoleShadow } from "@/lib/nodeStyles";
import { FieldPath } from "@/components/graph/FieldPath";
import { useFitOnContainerResize } from "@/hooks/useFitOnContainerResize";

function BoundaryNode({ data }: { data: { label: string; side: string } }) {
  return <div className="relative w-[220px] rounded-xl border border-sky-200 bg-white px-4 py-3 shadow-sm">{data.side === "target" && <Handle type="target" position={Position.Left} className="!h-2.5 !w-2.5 !border-2 !border-white !bg-sky-500" />}<p className="text-[10px] font-bold uppercase tracking-wide text-sky-700">{data.side} system</p><p className="mt-1 truncate text-sm font-bold text-slate-800" title={data.label}>{data.label}</p><p className="mt-2 text-[10px] text-slate-500">{data.side === "source" ? "Produces this data" : "Receives this data"}</p>{data.side === "source" && <Handle type="source" position={Position.Right} className="!h-2.5 !w-2.5 !border-2 !border-white !bg-sky-500" />}</div>;
}

function ArtifactNode({ data }: { data: { record: LandscapeProvenance; selected?: boolean; dimmed?: boolean; assistantVisualRole?: string } }) {
  const record = data.record;
  return <div data-assistant-role={data.assistantVisualRole} style={{ boxShadow: assistantRoleShadow(data.assistantVisualRole) }} className={`relative w-[280px] rounded-xl border bg-white px-4 py-3 shadow-sm transition ${data.selected && !data.assistantVisualRole ? "border-cyan-500 ring-4 ring-cyan-100" : data.assistantVisualRole ? "border-cyan-500" : "border-cyan-200"} ${data.dimmed ? "opacity-20" : "opacity-100"}`}><Handle type="target" position={Position.Left} className="!h-2.5 !w-2.5 !border-2 !border-white !bg-cyan-500" /><p className="text-[10px] font-bold uppercase tracking-wide text-cyan-700">Implementing design</p><p className="mt-1 line-clamp-2 text-sm font-bold text-slate-800">{record.artifact_id}</p><p className="mt-1 text-[10px] leading-snug text-slate-500">Select to inspect purpose and mappings.</p>{record.component_name && <p className="mt-1 truncate rounded bg-cyan-50 px-1.5 py-0.5 text-[9px] text-cyan-800">{record.component_name}</p>}<Handle type="source" position={Position.Right} className="!h-2.5 !w-2.5 !border-2 !border-white !bg-cyan-500" /></div>;
}
const nodeTypes = { integrationBoundary: memo(BoundaryNode), integrationArtifact: memo(ArtifactNode) };

function DeliveryPanel({ landscape, edge }: { landscape: ProjectLandscapeResponse; edge: TopologyEdge }) {
  const stream = edge.businessStream;
  const path = stream && landscape.business_topology ? streamDeliveryPath(landscape.business_topology, stream) : undefined;
  if (!path) return <Empty text="No ordered delivery path was established for this stream." />;
  return (
    <section className="mx-auto max-w-4xl p-5">
      <h2 className="text-sm font-bold text-slate-800">Delivery path</h2>
      <p className="mt-1 text-xs text-slate-500">Technical mechanisms are shown in order; they do not replace the scanner graph.</p>
      {path.description && <p className="mt-2 text-xs text-slate-600">{path.description}</p>}
      <ol className="mt-4 space-y-2">
        {[...path.segments].sort((a, b) => a.sequence - b.sequence).map((segment) => (
          <li key={segment.id} className="rounded-lg border bg-white p-3 text-xs">
            <p className="font-semibold text-slate-800">{segment.sequence}. {landscapeEntityLabel(landscape, segment.source_ref)} → {landscapeEntityLabel(landscape, segment.destination_ref)}</p>
            <p className="mt-1 text-slate-600">{[
              topologyTerm(segment.mechanism),
              segment.protocol !== "unknown" ? segment.protocol : "",
              segment.mode !== "unknown" ? DELIVERY_MODE_LABEL[segment.mode] : "",
              segment.component,
            ].filter(Boolean).join(" · ")}</p>
            {segment.description && <p className="mt-1 text-slate-500">{segment.description}</p>}
          </li>
        ))}
      </ol>
    </section>
  );
}

/**
 * Controls, risks, runtime, conflicts and open questions for one stream, all
 * resolved by `streamStructuredFacts` -- the same projection the L1 panel
 * uses. This panel used to run its own id lookup, a second copy of the rule.
 */
function ControlsPanel({ landscape, edge }: { landscape: ProjectLandscapeResponse; edge: TopologyEdge }) {
  const stream = edge.businessStream;
  const facts = stream && landscape.business_topology
    ? streamStructuredFacts(landscape.business_topology, stream)
    : null;
  return (
    <section className="mx-auto max-w-4xl p-5">
      <h2 className="text-sm font-bold text-slate-800">Controls, runtime, and open questions</h2>
      <RuntimeMetricGrid metrics={facts?.runtimeMetrics ?? []} />
      <FindingList
        title="Controls"
        items={(facts?.controls ?? []).map((item) => ({ ...item, badge: item.control_type }))}
      />
      <FindingList
        title="Risks"
        tone="amber"
        items={(facts?.risks ?? []).map((item) => ({ ...item, badge: item.severity !== "unknown" ? RISK_SEVERITY_LABEL[item.severity] : "" }))}
      />
      <FindingList
        title="Runtime observations"
        tone="sky"
        items={(facts?.runtimeObservations ?? []).map((item) => ({
          ...item,
          description: item.observed_behavior || item.description,
          badge: item.kind && item.kind !== "unknown" ? RUNTIME_OBSERVATION_KIND_LABEL[item.kind] : "",
          detail: [item.observation_window, item.source].filter(Boolean).join(" · "),
        }))}
      />
      <FindingList
        title="Discrepancies"
        tone="red"
        items={(facts?.conflicts ?? []).map((item) => ({
          ...item,
          badge: CONFLICT_TYPE_LABEL[item.conflict_type],
          detail: item.resolution ? `Resolution: ${item.resolution}` : "",
        }))}
      />
      <FindingList
        title="Not established"
        tone="slate"
        items={(facts?.unknowns ?? []).map((item) => ({
          ...item,
          // The question is the fact; the description is context for it.
          description: item.question || item.description,
          detail: item.question && item.description && item.description !== item.question ? item.description : "",
        }))}
      />
    </section>
  );
}

/**
 * Measured production numbers, each with the window it covers. Renders nothing
 * at all when the mirror reported none: a metric grid of dashes reads as an
 * estate with no traffic rather than an analysis with no measurement.
 */
function RuntimeMetricGrid({ metrics }: { metrics: StreamStructuredFacts["runtimeMetrics"] }) {
  if (metrics.length === 0) return null;
  return (
    <section className="mt-5">
      <h3 className="text-xs font-bold text-slate-700">In productie</h3>
      <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
        {metrics.map((metric) => (
          <div key={metric.id} className="rounded-lg border border-sky-100 bg-sky-50/60 p-2.5">
            <p className="truncate text-[9px] font-semibold uppercase tracking-wide text-sky-700" title={metric.label}>{metric.label}</p>
            <p className="mt-0.5 truncate text-sm font-bold text-slate-800">{[metric.value, metric.unit].filter(Boolean).join(" ")}</p>
            {metric.window && <p className="mt-0.5 truncate text-[10px] text-slate-500" title={metric.window}>{metric.window}</p>}
            {metric.source && <p className="truncate text-[9px] text-slate-400" title={metric.source}>{metric.source}</p>}
            {metric.note && <p className="mt-0.5 text-[9px] leading-relaxed text-slate-500">{metric.note}</p>}
          </div>
        ))}
      </div>
    </section>
  );
}

interface FindingItem {
  id: string;
  name: string;
  description: string;
  /** A short classifier (control type, severity, kind); omitted when empty. */
  badge?: string;
  /** A secondary line (window/source, resolution); omitted when empty. */
  detail?: string;
}

function FindingList({ title, items, tone = "emerald" }: { title: string; items: FindingItem[]; tone?: "emerald" | "amber" | "slate" | "sky" | "red" }) {
  const styles = tone === "amber" ? "border-amber-200 bg-amber-50" : tone === "slate" ? "border-slate-200 bg-slate-50" : tone === "sky" ? "border-sky-200 bg-sky-50" : tone === "red" ? "border-red-200 bg-red-50" : "border-emerald-200 bg-emerald-50";
  return <section className="mt-5"><h3 className="text-xs font-bold text-slate-700">{title}</h3>{items.length === 0 ? <p className="mt-2 text-xs text-slate-500">None established for this stream.</p> : <div className="mt-2 space-y-2">{items.map((item) => <div key={item.id} className={`rounded-lg border p-3 text-xs ${styles}`}><div className="flex items-start justify-between gap-2"><p className="font-semibold text-slate-800">{item.name || "Unnamed finding"}</p>{item.badge && <span className="shrink-0 rounded bg-white/70 px-1.5 py-0.5 text-[10px] font-semibold text-slate-600">{item.badge}</span>}</div>{item.description && <p className="mt-1 text-slate-700">{item.description}</p>}{item.detail && <p className="mt-1 text-[11px] text-slate-500">{item.detail}</p>}</div>)}</div>}</section>;
}

export type IntegrationDetailTab = "flow" | "delivery" | "velden" | "regels" | "controls" | "evidence" | "designs";

type FieldFilter = "all" | "reaches" | "not_read" | "disabled";

/** Derived from the badge vocabulary: a chip and the cell it filters on must
 * never be able to say the same fact in two different words. */
const FIELD_FILTER_LABEL: Record<FieldFilter, string> = {
  all: "Alle",
  reaches: REACHES_TARGET_LABEL.yes,
  not_read: REACHES_TARGET_LABEL.no,
  disabled: REACHES_TARGET_LABEL.disabled,
};

const FIELD_FILTER_PREDICATE: Record<FieldFilter, (row: StreamFieldRow) => boolean> = {
  all: () => true,
  reaches: (row) => row.reachesTarget === "yes",
  not_read: (row) => row.reachesTarget === "no",
  disabled: (row) => row.reachesTarget === "disabled",
};

function reachesToneClasses(reaches: ReachesTarget): string {
  if (reaches === "yes") return "bg-emerald-50 text-emerald-700";
  if (reaches === "no" || reaches === "disabled") return "bg-red-50 text-red-700";
  if (reaches === "partial") return "bg-amber-50 text-amber-700";
  return "bg-slate-100 text-slate-500";
}

/**
 * The full field table for one stream: every row the producer published, with
 * the funnel column (`reaches target`) as a first-class filter. Value domains
 * are a separate sub-table nested under the field they constrain -- listed
 * beside field mappings, a status table reads as a business decision.
 */
function FieldsPanel({ facts }: { facts: StreamStructuredFacts }) {
  const [filter, setFilter] = useState<FieldFilter>("all");
  const rows = facts.fieldRows.filter(FIELD_FILTER_PREDICATE[filter]);
  return (
    <section className="mx-auto max-w-6xl p-5">
      <h2 className="text-sm font-bold text-slate-800">Velden</h2>
      <p className="mt-1 text-xs text-slate-500">Elke gepubliceerde veldregel van deze uitwisseling, met de bron of expressie die hem vult.</p>
      {facts.fieldRows.length > 0 && (
        <>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {(Object.keys(FIELD_FILTER_LABEL) as FieldFilter[]).map((item) => {
              const count = facts.fieldRows.filter(FIELD_FILTER_PREDICATE[item]).length;
              return (
                <button
                  key={item}
                  type="button"
                  onClick={() => setFilter(item)}
                  aria-pressed={filter === item}
                  className={`rounded-full px-2.5 py-1 text-[11px] font-semibold ${filter === item ? "bg-slate-900 text-white" : "border border-slate-200 bg-white text-slate-600 hover:bg-slate-50"}`}
                >
                  {FIELD_FILTER_LABEL[item]} <span className="opacity-60">{count}</span>
                </button>
              );
            })}
          </div>
          <div className="mt-3 overflow-x-auto rounded-lg border border-slate-200 bg-white">
            <table className="w-full text-left text-[11px]">
              <thead className="bg-slate-50 text-[10px] font-bold uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-2 py-2">#</th>
                  <th className="px-2 py-2">Doelveld</th>
                  <th className="px-2 py-2">Bron of expressie</th>
                  <th className="px-2 py-2">Soort</th>
                  <th className="px-2 py-2">Categorie</th>
                  <th className="px-2 py-2">Bereikt doel</th>
                  <th className="px-2 py-2">Toelichting</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={`${row.transformationId}:${row.index}`} className="border-t border-slate-100 align-top">
                    <td className="px-2 py-1.5 text-slate-400">{row.index}</td>
                    <td className="px-2 py-1.5"><FieldPath value={row.targetField} className="font-semibold text-slate-800" /></td>
                    <td className="px-2 py-1.5"><FieldPath value={row.sourceOrExpression} className="text-slate-600" /></td>
                    <td className="px-2 py-1.5 text-slate-500">{row.mappingType}</td>
                    <td className="px-2 py-1.5 text-slate-500">{row.category || "—"}</td>
                    <td className="px-2 py-1.5">
                      <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${reachesToneClasses(row.reachesTarget)}`}>{REACHES_TARGET_LABEL[row.reachesTarget]}</span>
                    </td>
                    <td className="px-2 py-1.5 text-slate-500">{row.note || ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {rows.length === 0 && <p className="mt-2 text-xs text-slate-500">Geen veldregels met dit filter.</p>}
        </>
      )}
      {facts.valueDomainGroups.length > 0 && (
        <section className="mt-6">
          <h3 className="text-xs font-bold text-slate-700">Waardetabel</h3>
          <p className="mt-1 text-[11px] text-slate-500">Toegestane waarden, genest onder het doelveld dat ze begrenzen.</p>
          <div className="mt-3 space-y-3">
            {facts.valueDomainGroups.map((group) => (
              <div key={group.targetField} className="rounded-lg border border-slate-200 bg-white p-3">
                <p className="text-[11px] font-bold text-slate-800">{group.targetField}</p>
                {group.transformations.map((transformation) => (
                  <div key={transformation.id} className="mt-2">
                    {transformation.name && transformation.name !== group.targetField && (
                      <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">{transformation.name}</p>
                    )}
                    <table className="mt-1 w-full text-left text-[11px]">
                      <tbody>
                        {transformation.mappings.map((mapping, index) => (
                          <tr key={`${transformation.id}:${index}`} className="border-t border-slate-100">
                            <td className="py-1 pr-3"><code className="break-all text-slate-600">{mapping.expression || mapping.source}</code></td>
                            <td className="w-4 py-1 text-slate-400">→</td>
                            <td className="py-1"><code className="break-all text-slate-800">{mapping.target}</code></td>
                            <td className="py-1 pl-3 text-[10px] text-slate-400">{mapping.note ?? ""}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ))}
              </div>
            ))}
          </div>
        </section>
      )}
    </section>
  );
}

/**
 * The decisions the integration layer takes on behalf of the business, each
 * with the owner who can confirm or retire it, plus the conditions that decide
 * whether the exchange happens at all.
 */
function RulesPanel({ facts, conditions }: { facts: StreamStructuredFacts; conditions: StreamCondition[] }) {
  return (
    <section className="mx-auto max-w-6xl p-5">
      <h2 className="text-sm font-bold text-slate-800">Businessregels</h2>
      <p className="mt-1 text-xs text-slate-500">Beslissingen die de integratielaag namens de business neemt. De eigenaarsvraag is de zin om aan die eigenaar voor te leggen.</p>
      {facts.businessRules.length > 0 && (
        <div className="mt-3 overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="w-full text-left text-[11px]">
            <thead className="bg-slate-50 text-[10px] font-bold uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-2 py-2">Soort</th>
                <th className="px-2 py-2">Regel</th>
                <th className="px-2 py-2">Invoer</th>
                <th className="px-2 py-2">Uitvoer</th>
                <th className="px-2 py-2">Eigenaar</th>
              </tr>
            </thead>
            <tbody>
              {facts.businessRules.map((rule) => (
                <tr key={rule.id} className="border-t border-slate-100 align-top">
                  <td className="px-2 py-1.5 font-semibold text-slate-600">{BUSINESS_RULE_KIND_LABEL[rule.kind ?? "unknown"]}</td>
                  <td className="px-2 py-1.5 text-slate-800">
                    {rule.statement || rule.description || rule.name}
                    {rule.owner_question && <p className="mt-1 text-[10px] italic text-slate-500">{rule.owner_question}</p>}
                  </td>
                  <td className="px-2 py-1.5 text-slate-600">{rule.inputs.join(", ") || "—"}</td>
                  <td className="px-2 py-1.5 text-slate-600">{rule.output || "—"}</td>
                  <td className="px-2 py-1.5 text-slate-600">{rule.owner || "Niet vastgesteld"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {conditions.length > 0 && (
        <section className="mt-6">
          <h3 className="text-xs font-bold text-slate-700">Voorwaarden</h3>
          <p className="mt-1 text-[11px] text-slate-500">Wanneer deze uitwisseling plaatsvindt, en in welke volgorde.</p>
          <div className="mt-3 overflow-x-auto rounded-lg border border-slate-200 bg-white">
            <table className="w-full text-left text-[11px]">
              <thead className="bg-slate-50 text-[10px] font-bold uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-2 py-2">Soort</th>
                  <th className="px-2 py-2">Voorwaarde</th>
                  <th className="px-2 py-2">Evidentie</th>
                </tr>
              </thead>
              <tbody>
                {conditions.map((condition, index) => (
                  <tr key={`${condition.kind ?? "unknown"}:${index}`} className="border-t border-slate-100 align-top">
                    <td className="px-2 py-1.5 font-semibold text-slate-600">{STREAM_CONDITION_KIND_LABEL[condition.kind ?? "unknown"]}</td>
                    <td className="px-2 py-1.5 text-slate-800">{condition.statement}</td>
                    <td className="px-2 py-1.5 text-slate-500">{condition.evidence_note || ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </section>
  );
}

function EndpointEvidence({ landscape, edge }: { landscape: ProjectLandscapeResponse; edge: TopologyEdge }) {
  const detail = integrationDetailForEdge(landscape, edge);
  if (detail.channels.length === 0 && detail.interfaces.length === 0) return <p className="mt-3 text-xs text-slate-500">No channel or endpoint evidence is attached to this stream.</p>;
  return <div className="mt-3 space-y-2">{detail.channels.map((item) => <div key={`channel:${item.qualified_artifact_id}:${item.channel}`} className="rounded border bg-white p-2 text-xs"><p className="font-semibold text-slate-700">Channel · {item.channel}</p><p className="mt-1 text-slate-500">{topologyTerm(item.channel_type)} · {topologyTerm(item.direction)} · {item.component_name || item.artifact_id}</p>{item.redaction_descriptor && <p className="mt-1 text-[10px] text-slate-500">Endpoint redacted; protocol context retained ({item.redaction_descriptor.scheme || "scheme unknown"}, {item.redaction_descriptor.tls === true ? "TLS" : item.redaction_descriptor.tls === false ? "no TLS" : "TLS not established"}).</p>}</div>)}{detail.interfaces.map((item) => <div key={`interface:${item.qualified_artifact_id}:${item.endpoint_type}`} className="rounded border bg-white p-2 text-xs"><p className="font-semibold text-slate-700">Endpoint · {topologyTerm(item.endpoint_type)}</p><p className="mt-1 text-slate-500">{topologyTerm(item.direction)} · {item.component_name || item.artifact_id}</p>{item.redaction_descriptor && <p className="mt-1 text-[10px] text-slate-500">Endpoint value redacted; structural descriptor retained ({item.redaction_descriptor.scheme || "scheme unknown"}).</p>}</div>)}</div>;
}

/**
 * Mapping rows as ONE table per kind, not one card per row.
 *
 * The previous rendering gave every row its own bordered card carrying the
 * group label and a "type . origin" footer, so a design with 28 mappings
 * printed the same two labels 28 times and needed a long scroll for what is a
 * two-column table. The group label and the origin belong to the GROUP; only
 * a row-level exception (a different mapping type, or a value that does not
 * reach the target) is worth repeating per row.
 *
 * A `value_domain` group is headed by the field whose values it defines, so a
 * status table cannot read as a list of unrelated field mappings.
 */
/**
 * What each group of rows IS, and where it was read from.
 *
 * Without this a reader sees "SCANNER FIELD LINEAGE" over thirteen rows of
 * `row6/CurrentDate -> out1/YEAR` and has no way to know whether that is a
 * contract, a defect or noise. The panel's chrome is English throughout
 * ("Implementing design", "Kind", "Role"); the row CONTENT is in the language
 * the analysis was written in.
 */
const MAPPING_GROUP_BLURB: Record<string, string> = {
  field_mapping: "What this interface's transformation writes: a source field into a target field.",
  value_domain: "The values this one target field can take, and what each source value becomes.",
  derived_rule: "Targets that are computed rather than copied. The rule is shown in place of a source field.",
  constant_set: "Values the transformation fills in itself, with no source field behind them.",
  scanner_lineage: "Read automatically out of the job: which column ends up in which other column, and in which step. Useful as an independent check on what the job actually does -- it is wiring, not an agreed contract, and it includes internal plumbing.",
};

/** One row, or several rows that share a source and differ only in target. */
interface MappingRowGroup {
  key: string;
  row: ImplementingDesignMapping;
  targets: string[];
}

/**
 * Mapping rows as ONE table per kind, not one card per row.
 *
 * The previous rendering gave every row its own bordered card carrying the
 * group label and a "type . origin" footer, so a design with 28 mappings
 * printed the same two labels 28 times and needed a long scroll for what is a
 * two-column table. Four things are collapsed here because repeating them is
 * what made the panel unreadable: the group label, the origin, a source that
 * fans out into many targets, and a source identical to its target.
 */
/** Categories a message route produces; none of them is a field mapping. */
const MESSAGE_CATEGORIES = new Set(["berichtkop", "berichtinhoud", "bewerking"]);

/**
 * What to call this count. A Camel route has no tMap: it sets message headers,
 * fills the body and runs its own code. Labelling those "48 mappings" tells the
 * reader that 48 fields are translated from sender to receiver, which is not
 * what happens and not what the evidence says.
 */
function mappingCountLabel(mappings: ImplementingDesignMapping[]): string {
  const message = mappings.filter((mapping) => MESSAGE_CATEGORIES.has(mapping.category)).length;
  const fields = mappings.length - message;
  if (fields > 0 && message > 0) return `${fields} veld${fields === 1 ? "" : "en"} · ${message} berichtstap${message === 1 ? "" : "pen"}`;
  if (message > 0) return `${message} berichtstap${message === 1 ? "" : "pen"}`;
  return `${fields} veldafbeelding${fields === 1 ? "" : "en"}`;
}

function MappingGroups({ mappings }: { mappings: ImplementingDesignMapping[] }) {
  const groups: Array<{
    key: string;
    label: string;
    sublabel: string;
    blurb: string;
    origin: string;
    scanner: boolean;
    rows: ImplementingDesignMapping[];
  }> = [];
  const byKey = new Map<string, number>();
  for (const mapping of mappings) {
    const scanner = mapping.origin === "scanner_lineage";
    // A value table is keyed by the field it constrains; everything else by its
    // group label, so two value tables on different fields stay apart.
    const key = mapping.transformationKind === "value_domain"
      ? `value:${mapping.targetField || mapping.transformationId}`
      : `${mapping.group}:${mapping.origin}`;
    let index = byKey.get(key);
    if (index === undefined) {
      index = groups.length;
      byKey.set(key, index);
      groups.push({
        key,
        label: mapping.group,
        sublabel: mapping.transformationKind === "value_domain" ? mapping.targetField : "",
        blurb: scanner ? MAPPING_GROUP_BLURB.scanner_lineage : MAPPING_GROUP_BLURB[mapping.transformationKind] ?? "",
        origin: scanner ? "scanner lineage" : "structured transformation",
        scanner,
        rows: [],
      });
    }
    groups[index].rows.push(mapping);
  }
  return (
    <div className="mt-3 space-y-3">
      {groups.map((group) => (
        <MappingGroupTable key={group.key} group={group} />
      ))}
    </div>
  );
}

function MappingGroupTable({ group }: {
  group: {
    label: string;
    sublabel: string;
    blurb: string;
    origin: string;
    scanner: boolean;
    rows: ImplementingDesignMapping[];
  };
}) {
  // Scanner lineage is internal wiring: worth having, not worth reading first.
  const [open, setOpen] = useState(!group.scanner);
  const types = new Set(group.rows.map((row) => row.mappingType));
  const commonType = types.size === 1 ? [...types][0] : "";

  // One source that fans out into many targets is ONE fact. Eight rows for a
  // timestamp exploded into YEAR/MONTH/DAY/HOUR/MINUTE/SECOND is the same fact
  // eight times.
  const rowGroups: MappingRowGroup[] = [];
  const bySource = new Map<string, number>();
  for (const row of group.rows) {
    const label = row.expression || row.source;
    // ONLY scanner-read rows merge. An analyst wrote each curated row
    // deliberately, and two of them can share a source label without being one
    // fact: this merged an interface's username and password into a single row
    // because both sources were the same redaction placeholder.
    const mergeable = row.origin === "scanner_lineage" && Boolean(label);
    const fanKey = `${label}\u0000${row.mappingType}\u0000${row.note}\u0000${row.component}`;
    const index = mergeable ? bySource.get(fanKey) : undefined;
    if (index === undefined) {
      if (mergeable) bySource.set(fanKey, rowGroups.length);
      rowGroups.push({ key: `${fanKey}:${rowGroups.length}`, row, targets: [row.target] });
    } else {
      rowGroups[index].targets.push(row.target);
    }
  }

  return (
    <div className="rounded-xl border border-slate-200">
      <button
        type="button"
        onClick={() => setOpen((previous) => !previous)}
        aria-expanded={open}
        data-testid={`mapping-group:${group.label}`}
        className="flex w-full items-start justify-between gap-2 border-b border-slate-100 px-2.5 py-1.5 text-left hover:bg-slate-50"
      >
        <div className="min-w-0">
          <p className="truncate text-[9px] font-bold uppercase tracking-wide text-slate-500">{group.label}</p>
          {group.sublabel && <FieldPath value={group.sublabel} className="mt-0.5 block text-[10px] text-slate-700" />}
        </div>
        <span className="shrink-0 text-[10px] font-semibold text-slate-400">{group.rows.length}</span>
      </button>
      {group.blurb && <p className="px-2.5 pt-1.5 text-[9px] leading-relaxed text-slate-500">{group.blurb}</p>}
      {open && (
        <div className="mt-1.5 divide-y divide-slate-50">
          {rowGroups.map((entry) => <MappingRow key={entry.key} entry={entry} commonType={commonType} />)}
        </div>
      )}
      <p className="mt-1 border-t border-slate-100 px-2.5 py-1 text-[9px] text-slate-400">
        {[commonType, group.origin].filter(Boolean).join(" \u00b7 ")}
        {!open && ` \u00b7 ${group.rows.length} rows hidden, tap the header`}
      </p>
    </div>
  );
}

/** A redaction placeholder is a FINDING, not a field name. */
const REDACTED_SOURCE = /^\s*\[\s*REDACTED/i;

function MappingRow({ entry, commonType }: { entry: MappingRowGroup; commonType: string }) {
  const row = entry.row;
  const label = row.expression || row.source;
  const redacted = REDACTED_SOURCE.test(label);
  // Every row keeps source and target SIDE BY SIDE, including a field that
  // crosses under its own name: a table you can scan down two columns is the
  // reason this reads at all, and collapsing some rows to one column breaks
  // the alignment for all of them.
  const trailing = row.mappingType !== commonType ? row.mappingType : "";
  const reach = row.reachesTarget === "no" || row.reachesTarget === "disabled"
    ? REACHES_TARGET_LABEL[row.reachesTarget]
    : "";

  return (
    <div className="px-2.5 py-1.5">
      <div className="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-baseline gap-1.5">
        {redacted ? (
          <span className="break-words text-[10px] font-semibold text-amber-700" title={label}>value withheld</span>
        ) : (
          <FieldPath value={label} className="text-[10px] text-slate-600" />
        )}
        <ChevronRight className="h-3 w-3 shrink-0 self-center text-cyan-600" />
        <span className="min-w-0">
          <FieldPath value={entry.targets[0]} className="text-[10px] text-slate-800" />
          {entry.targets.length > 1 && <span className="text-[10px] text-slate-400">{` +${entry.targets.length - 1}`}</span>}
        </span>
      </div>
      {(trailing || reach || entry.targets.length > 1 || row.component || row.note) && (
        <div className="mt-0.5 space-y-0.5 text-[9px] leading-relaxed">
          {(trailing || reach) && (
            <p className="uppercase text-slate-400">{[trailing, reach].filter(Boolean).join(" \u00b7 ")}</p>
          )}
          {entry.targets.length > 1 && (
            <p className="text-slate-500">{`Into ${entry.targets.length} fields: ${entry.targets.join(", ")}`}</p>
          )}
          {row.component && <p className="text-slate-400">{row.component}</p>}
          {/* Notes WRAP. Truncating one mid-phrase hid the reason, which for a
              redacted constant is the entire point of the row. */}
          {row.note && <p className={redacted ? "text-amber-700" : "text-slate-500"}>{row.note}</p>}
        </div>
      )}
    </div>
  );
}

/**
 * The documents a stream's structured facts were read from. Provenance, so it
 * lives on the evidence tab: the L1 panel omits it by request.
 */
function DocumentReferences({ refs }: { refs: string[] }) {
  if (refs.length === 0) return null;
  return (
    <section className="mt-5">
      <h3 className="text-xs font-bold text-slate-700">Documentation references</h3>
      <ul className="mt-2 space-y-1">
        {refs.map((ref) => <li key={ref} className="break-all rounded border bg-white p-2 text-[11px] text-slate-600">{ref}</li>)}
      </ul>
    </section>
  );
}

function Empty({ text }: { text: string }) { return <div className="p-5 text-sm text-slate-500">{text}</div>; }

function businessObjectNames(landscape: ProjectLandscapeResponse, edge: TopologyEdge): string {
  const ids = edge.businessStream?.business_object_ids ?? [];
  if (ids.length === 0) return "";
  const objectsById = new Map((landscape.business_topology?.business_objects ?? []).map((item) => [item.id, item.name]));
  return ids.map((id) => objectsById.get(id)).filter(Boolean).join(", ");
}

function DetailTag({ children }: { children: ReactNode }) {
  return <span className="rounded-md bg-slate-100 px-2 py-1 text-[10px] font-semibold text-slate-600">{children}</span>;
}

function DesignExplanationPanel({
  designs,
  selected,
  sourceLabel,
  targetLabel,
  streamSummary,
  onSelect,
  onClose,
  onOpen,
}: {
  designs: ImplementingDesignDetail[];
  selected: ImplementingDesignDetail | null;
  sourceLabel: string;
  targetLabel: string;
  streamSummary: string;
  onSelect: (design: ImplementingDesignDetail) => void;
  onClose: () => void;
  onOpen: (record: LandscapeProvenance) => void;
}) {
  return (
    <aside className="absolute bottom-0 right-0 top-[102px] z-20 w-[410px] overflow-y-auto border-l border-slate-200 bg-white shadow-xl" aria-label="Integration explanation">
      <div className="sticky top-0 z-10 flex items-start justify-between gap-3 border-b border-slate-100 bg-white px-4 py-3">
        <div className="min-w-0">
          <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-cyan-700">{selected ? "Implementing design" : "Integration overview"}</p>
          <h2 className="mt-1 truncate text-sm font-bold text-slate-900">{selected?.label || `${sourceLabel} → ${targetLabel}`}</h2>
        </div>
        <button type="button" onClick={onClose} className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700" aria-label="Close integration explanation"><X className="h-4 w-4" /></button>
      </div>
      {!selected ? (
        <div className="space-y-5 p-4">
          <p className="text-xs leading-relaxed text-slate-600">{streamSummary || "This view shows the designs implementing the selected data stream."}</p>
          <section>
            <h3 className="text-[11px] font-bold uppercase tracking-wide text-slate-500">Implementing designs</h3>
            <p className="mt-1 text-[10px] leading-relaxed text-slate-500">Select a design to understand its role and mappings before opening the technical graph.</p>
            <div className="mt-3 space-y-2">
              {designs.map((design) => (
                <button key={design.record.qualified_artifact_id} type="button" onClick={() => onSelect(design)} className="flex w-full items-center justify-between gap-3 rounded-xl border border-slate-200 p-3 text-left transition hover:border-cyan-300 hover:bg-cyan-50/40">
                  <div className="min-w-0"><p className="truncate text-xs font-bold text-slate-800">{design.label}</p><p className="mt-1 truncate text-[10px] text-slate-500">{design.artifactKind} · {design.artifactRole}</p></div>
                  <div className="flex shrink-0 items-center gap-2"><span className="rounded bg-slate-100 px-2 py-1 text-[10px] font-semibold text-slate-600">{mappingCountLabel(design.mappings)}</span><ChevronRight className="h-4 w-4 text-slate-400" /></div>
                </button>
              ))}
            </div>
          </section>
        </div>
      ) : (
        <div className="space-y-5 p-4">
          <p className="text-xs leading-relaxed text-slate-600">{selected.description}</p>
          <div className="grid grid-cols-2 gap-2 text-[10px]"><div className="rounded-lg bg-slate-50 p-2"><p className="font-semibold uppercase text-slate-400">Kind</p><p className="mt-1 font-bold text-slate-700">{topologyTerm(selected.artifactKind)}</p></div><div className="rounded-lg bg-slate-50 p-2"><p className="font-semibold uppercase text-slate-400">Role</p><p className="mt-1 font-bold text-slate-700">{selected.businessRole || topologyTerm(selected.artifactRole)}</p></div></div>
          <section>
            <div className="flex items-center justify-between gap-3"><h3 className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-wide text-slate-600"><Code2 className="h-3.5 w-3.5 text-cyan-600" /> Afbeeldingen en berichtopbouw</h3><span className="text-[10px] font-semibold text-slate-400">{mappingCountLabel(selected.mappings)}</span></div>
            {selected.mappings.length > 0 ? <MappingGroups mappings={selected.mappings} /> : <div className="mt-3 rounded-xl border border-dashed border-slate-200 p-3 text-xs text-slate-500">{selected.mappingAttribution === "ambiguous" ? "Mappings exist for a duplicate job name, but the available evidence cannot safely assign them to this source root." : selected.invokedDesigns.length > 0 ? "This route delegates processing to the design shown below. No field-level mapping was extracted for that path." : "No field or value mappings were established for this design."}</div>}
            {(selected.invokedDesigns.length > 0 || selected.mappingComponents.length > 0) && <div className="mt-3 rounded-xl bg-cyan-50/70 p-3 text-[10px]"><p className="font-bold uppercase tracking-wide text-cyan-800">Mapping implementation</p>{selected.invokedDesigns.length > 0 && <p className="mt-1 text-cyan-900"><b>Invokes:</b> {selected.invokedDesigns.join(", ")}</p>}{selected.mappingComponents.length > 0 && <p className="mt-1 text-cyan-900"><b>Relevant components:</b> {selected.mappingComponents.join(", ")}</p>}</div>}
          </section>
          <section><h3 className="text-[11px] font-bold uppercase tracking-wide text-slate-500">How it participates</h3><div className="mt-2 flex flex-wrap gap-1.5">{selected.integrationSolutions.map((item) => <DetailTag key={item}>{item}</DetailTag>)}{selected.protocols.map((item) => <DetailTag key={item}>{item}</DetailTag>)}{selected.channels.map((item) => <DetailTag key={`${item.channel}:${item.component_name}`}>{topologyTerm(item.channel_type)} · {item.channel}</DetailTag>)}{selected.interfaces.map((item) => <DetailTag key={`${item.endpoint_type}:${item.component_name}`}>{topologyTerm(item.endpoint_type)} endpoint</DetailTag>)}</div>{selected.deliverySegments.length > 0 && <ol className="mt-3 space-y-2">{selected.deliverySegments.map((segment) => <li key={segment.id} className="rounded-lg border border-slate-100 p-2 text-[10px]"><p className="font-semibold text-slate-700">{segment.name}</p><p className="mt-1 text-slate-500">{[segment.protocol, segment.component, segment.resource].filter(Boolean).join(" · ")}</p></li>)}</ol>}</section>
          {selected.record.fact_type === "business_topology_solution"
            ? <p className="rounded-lg border border-dashed border-slate-200 bg-slate-50 p-3 text-[11px] leading-relaxed text-slate-600">This node is a declared integration solution, not a single scanner artifact, so there is no technical design to open. The implementing designs resolve once this project publishes its topology evidence catalogue.</p>
            : <button type="button" onClick={() => onOpen(selected.record)} className="w-full rounded-lg bg-slate-900 px-3 py-2 text-xs font-semibold text-white hover:bg-slate-700">Open full technical design</button>}
        </div>
      )}
    </aside>
  );
}

export function IntegrationDetailCanvas({ landscape, edge, onBack, onOpenArtifact, assistantEmphasis, assistantFocusRequest, onContextChange }: { landscape: ProjectLandscapeResponse; edge: TopologyEdge; onBack: () => void; onOpenArtifact: (record: LandscapeProvenance) => void; assistantEmphasis?: AssistantCanvasEmphasis | null; assistantFocusRequest?: AssistantFocusRequest | null; onContextChange?: (context: CopilotViewContext) => void }) {
  const [requestedTab, setTab] = useState<IntegrationDetailTab>("flow");
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(null);
  const [explanationOpen, setExplanationOpen] = useState(true);
  const [instance, setInstance] = useState<ReactFlowInstance | null>(null);

  // The diagram follows the docked copilot: widening that panel narrows this
  // column, and without a refit the graph slides out of view on the right.
  const refit = useCallback(
    () => void instance?.fitView({ padding: 0.15, duration: 300 }),
    [instance],
  );
  const canvasRef = useFitOnContainerResize(refit);
  const detail = useMemo(() => integrationDetailForEdge(landscape, edge), [landscape, edge]);
  const designs = useMemo(() => {
    const unique = new Map(detail.provenance.map((record) => [record.qualified_artifact_id, record]));
    return [...unique.values()].map((record) => implementingDesignDetail(landscape, edge, record));
  }, [detail.provenance, edge, landscape]);
  const selectedDesign = designs.find((design) => design.record.qualified_artifact_id === selectedArtifactId) ?? null;
  const [nodes, setNodes, onNodesChange] = useNodesState(detail.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(detail.edges);
  const sourceLabel = landscapeEntityLabel(landscape, edge.source);
  const targetLabel = landscapeEntityLabel(landscape, edge.target);
  const stream = edge.businessStream;
  // Resolved once, by the same projection the L1 flow panel uses, so the two
  // surfaces can never disagree about what this stream carries.
  const facts = useMemo<StreamStructuredFacts | null>(
    () => (stream && landscape.business_topology ? streamStructuredFacts(landscape.business_topology, stream) : null),
    [landscape.business_topology, stream],
  );
  const conditions = stream?.conditions ?? [];
  // A project that publishes no field or rule data does not get the tabs at
  // all -- an empty tab is a promise of content that is not there.
  const tabs = useMemo<IntegrationDetailTab[]>(() => [
    "flow",
    "delivery",
    ...((facts?.fieldRows.length ?? 0) > 0 || (facts?.valueDomainGroups.length ?? 0) > 0 ? ["velden" as const] : []),
    ...((facts?.businessRules.length ?? 0) > 0 || conditions.length > 0 ? ["regels" as const] : []),
    "controls",
    "evidence",
    "designs",
  ], [facts, conditions.length]);
  const activeTab = tabs.includes(requestedTab) ? requestedTab : "flow";
  useEffect(() => {
    setNodes(detail.nodes.map((node) => ({
      ...node,
      data: {
        ...node.data,
        selected: node.id === `artifact:${selectedArtifactId}`,
        dimmed: Boolean(assistantEmphasis?.nodeRoles.size) && !assistantEmphasis?.nodeRoles.has(node.id),
        assistantVisualRole: assistantEmphasis?.nodeRoles.get(node.id),
      },
    })));
    setEdges(detail.edges.map((flowEdge) => ({
      ...flowEdge,
      style: {
        ...flowEdge.style,
        opacity: assistantEmphasis?.edgeRoles.size
          ? assistantEmphasis.edgeRoles.has(flowEdge.id) ? 1 : 0.12
          : undefined,
      },
    })));
  }, [detail, selectedArtifactId, assistantEmphasis, setNodes, setEdges]);
  useEffect(() => {
    if (!assistantFocusRequest || !instance || activeTab !== "flow") return;
    const focusNodes = nodes.filter((node) => assistantFocusRequest.nodeIds.includes(node.id));
    if (focusNodes.length > 0) void instance.fitView({ nodes: focusNodes, padding: 0.35, duration: 450 });
  }, [assistantFocusRequest, instance, nodes, activeTab]);
  useEffect(() => {
    const record = detail.provenance.find((item) => item.qualified_artifact_id === selectedArtifactId);
    onContextChange?.({
      // The BARE design name, not `qualified_artifact_id`. The qualified form is
      // this canvas's own key (`<root>/<zip>/<group>::<name>`); the design index
      // the assistant reads is keyed on the name alone, and its substring tier
      // tests needle-in-name rather than name-in-needle -- so the long form can
      // never match and every question asked with a design selected resolved to
      // nothing.
      selectedEntity: record
        ? { kind: "job", label: record.artifact_id, entity_id: record.artifact_id }
        : undefined,
      activeDetailTab: activeTab,
    });
  }, [selectedArtifactId, detail.provenance, activeTab, onContextChange]);
  useEffect(() => {
    setSelectedArtifactId(null);
    setExplanationOpen(true);
  }, [edge]);
  const onDoubleClick: NodeMouseHandler = (_event, node) => { const record = (node.data as { record?: LandscapeProvenance }).record; if (record && record.fact_type !== "business_topology_solution") onOpenArtifact(record); };
  const onNodeClick: NodeMouseHandler = (_event, node) => {
    const record = (node.data as { record?: LandscapeProvenance }).record;
    if (!record) return;
    setSelectedArtifactId(record.qualified_artifact_id);
    setExplanationOpen(true);
  };
  const implementations = stream?.integration_solution_ids.map((id) => landscape.business_topology?.integration_solutions.find((item) => item.id === id)?.name).filter(Boolean) ?? [];
  const content = activeTab === "delivery" ? <DeliveryPanel landscape={landscape} edge={edge} /> : activeTab === "controls" ? <ControlsPanel landscape={landscape} edge={edge} /> : activeTab === "evidence" ? <section className="mx-auto max-w-4xl p-5"><h2 className="flex items-center gap-2 text-sm font-bold text-slate-800"><Network className="h-4 w-4" /> Technical evidence</h2><ArtifactList provenance={detail.provenance} onOpen={onOpenArtifact} /><h3 className="mt-5 text-xs font-bold text-slate-700">Channels and endpoints</h3><EndpointEvidence landscape={landscape} edge={edge} /><DocumentReferences refs={stream?.document_refs ?? []} />{detail.diagnostics.map((item, index) => <p key={index} className="mt-3 rounded border border-amber-100 bg-amber-50 p-2 text-xs text-amber-900"><FileWarning className="mr-1 inline h-3.5 w-3.5" />{item.description}</p>)}</section> : activeTab === "designs" ? <section className="p-5"><h2 className="text-sm font-bold text-slate-800">Implementing designs</h2><p className="mt-1 text-xs text-slate-500">Open a design to view the scanner-authored job or route graph.</p><ArtifactList provenance={detail.provenance} onOpen={onOpenArtifact} /></section> : activeTab === "velden" && facts ? <FieldsPanel facts={facts} /> : activeTab === "regels" && facts ? <RulesPanel facts={facts} conditions={conditions} /> : null;
  return <div ref={canvasRef} className="absolute inset-0" data-testid="integration-detail-canvas"><div className="absolute inset-x-0 top-0 z-20 border-b border-slate-200 bg-white/95 px-4 py-2 shadow-sm backdrop-blur"><div className="flex flex-wrap items-center gap-2"><button type="button" onClick={onBack} className="inline-flex items-center gap-1 rounded border border-slate-200 px-2 py-1 text-xs font-semibold text-slate-700 hover:bg-slate-50"><ArrowLeft className="h-3.5 w-3.5" /> Landscape</button><div className="min-w-0 flex-1"><p className="truncate text-sm font-bold text-slate-800">{sourceLabel} <span className="text-slate-400">→</span> {targetLabel}</p><p className="text-[10px] text-slate-500">L2 data stream · {stream?.name || topologyTerm(edge.dependency_type)}</p></div>{tabs.map((item) => <button key={item} type="button" onClick={() => setTab(item)} className={`rounded px-2 py-1 text-[11px] font-semibold capitalize ${activeTab === item ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"}`}>{item}</button>)}{activeTab === "flow" && !explanationOpen && <button type="button" onClick={() => setExplanationOpen(true)} className="rounded border border-cyan-200 px-2 py-1 text-[11px] font-semibold text-cyan-700 hover:bg-cyan-50">Explain</button>}</div>{stream && <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 border-t border-slate-100 pt-2 text-[10px] text-slate-600"><span><b>Purpose:</b> {stream.business_summary || "Not established"}</span><span><b>Implemented by:</b> {implementations.join(", ") || "Not established"}</span></div>}</div><div className={activeTab === "flow" ? `absolute bottom-0 left-0 top-[102px] transition-[right] ${explanationOpen ? "right-[410px]" : "right-0"}` : "absolute inset-x-0 bottom-0 top-[102px] overflow-auto bg-slate-50"}>{activeTab === "flow" ? <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onNodeClick={onNodeClick} onNodeDoubleClick={onDoubleClick} onInit={setInstance} fitView minZoom={0.1} maxZoom={3} panActivationKeyCode={null} onlyRenderVisibleElements proOptions={{ hideAttribution: true }}><Background variant={BackgroundVariant.Dots} gap={24} size={0.8} color="#cbd5e1" className="opacity-20" /><Controls showInteractive={false} /><MiniMap /></ReactFlow> : content}</div>{activeTab === "flow" && explanationOpen && <DesignExplanationPanel designs={designs} selected={selectedDesign} sourceLabel={sourceLabel} targetLabel={targetLabel} streamSummary={stream?.business_summary || ""} onSelect={(design) => setSelectedArtifactId(design.record.qualified_artifact_id)} onClose={() => setExplanationOpen(false)} onOpen={(record) => { if (record.fact_type !== "business_topology_solution") onOpenArtifact(record); }} />}</div>;
}

/**
 * Lists a set of relationships/edges for the user to descend into a single
 * L2 `IntegrationDetailCanvas`. Two scoping modes: `entityId` lists every
 * relationship touching one application/system (original use); `streamKeys`
 * (from an aggregated L1 system-pair edge, see `landscapeTransform`'s
 * `AggregatedRelationshipEdgeData`) scopes the list to exactly that pair's
 * flows. `streamKeys` takes precedence when both are supplied.
 */
export function IntegrationRelationshipChooser({ landscape, entityId, streamKeys, title, onBack, onChoose }: { landscape: ProjectLandscapeResponse; entityId?: string; streamKeys?: string[]; title: string; onBack: () => void; onChoose: (edge: TopologyEdge) => void }) {
  const relationships = streamKeys
    ? resolveRelationshipEdges(landscape, streamKeys)
    : entityId
      ? connectedEdges(landscape, entityId)
      : [];
  return <div className="absolute inset-0 overflow-auto bg-slate-50 p-4"><div className="mx-auto max-w-3xl"><button type="button" onClick={onBack} className="inline-flex items-center gap-1 rounded border border-slate-200 bg-white px-2 py-1 text-xs font-semibold text-slate-700 hover:bg-slate-50"><ArrowLeft className="h-3.5 w-3.5" /> Landscape</button><h2 className="mt-4 text-lg font-bold text-slate-800">Explore data streams for {title}</h2><p className="mt-1 text-sm text-slate-500">Choose a stream to see how it is delivered and which designs implement it.</p><div className="mt-4 space-y-2">{relationships.length === 0 ? <Empty text="No data streams are available." /> : relationships.map((edge) => { const objectNames = businessObjectNames(landscape, edge); return <button key={edge.businessStream?.id || `${edge.source}:${edge.target}:${edge.mechanism}`} type="button" onClick={() => onChoose(edge)} className="w-full rounded-xl border bg-white p-4 text-left shadow-sm transition hover:border-blue-300 hover:bg-blue-50/30"><p className="font-semibold text-slate-800">{landscapeEntityLabel(landscape, edge.source)} <span className="text-slate-400">→</span> {landscapeEntityLabel(landscape, edge.target)}</p><p className="mt-1 text-sm font-medium text-sky-800">{edge.businessStream?.name || topologyTerm(edge.dependency_type)}</p><p className="mt-1 text-xs text-slate-500">{objectNames || edge.businessStream?.business_summary || topologyTerm(edge.mechanism)} · {topologyTerm(edge.confidence)}</p></button>; })}</div></div></div>;
}

function ArtifactList({ provenance, onOpen }: { provenance: LandscapeProvenance[]; onOpen: (record: LandscapeProvenance) => void }) { return <section className="mt-4"><h3 className="flex items-center gap-2 text-xs font-bold text-slate-700"><Box className="h-4 w-4" /> Implementing artifacts</h3><div className="mt-2 space-y-2">{provenance.length === 0 ? <p className="text-xs text-slate-500">No scanner provenance is attached to this stream.</p> : provenance.map((record) => { const businessOnly = record.fact_type === "business_topology_solution"; return <div key={`${record.qualified_artifact_id}:${record.component_name}:${record.fact_type}`} className="flex items-center justify-between gap-3 rounded border bg-white p-2"><div className="min-w-0"><p className="truncate text-xs font-semibold text-slate-700">{record.artifact_id}</p><p className="truncate text-[10px] text-slate-500">{record.source_root}{record.component_name ? ` · ${record.component_name}` : ""}</p></div>{businessOnly ? <span className="shrink-0 rounded bg-sky-50 px-2 py-1 text-[10px] font-semibold text-sky-700">Business solution</span> : <button type="button" onClick={() => onOpen(record)} className="shrink-0 rounded border border-slate-200 px-2 py-1 text-[10px] font-semibold text-slate-700 hover:bg-slate-50">Open design</button>}</div>; })}</div></section>; }
