"use client";

import { memo, useEffect, useMemo, useState, type ReactNode } from "react";
import { ArrowLeft, Box, ChevronRight, Code2, FileWarning, Network, X } from "lucide-react";
import { Background, BackgroundVariant, Controls, Handle, MiniMap, Position, ReactFlow, type NodeMouseHandler, useEdgesState, useNodesState } from "@xyflow/react";
import { implementingDesignDetail, integrationDetailForEdge, type ImplementingDesignDetail } from "@/lib/integrationDetailTransform";
import type { LandscapeProvenance, ProjectLandscapeResponse } from "@/types/landscape";
import { connectedEdges, landscapeEntityLabel, resolveRelationshipEdges, topologyTerm, type TopologyEdge } from "@/lib/landscapeTransform";

function BoundaryNode({ data }: { data: { label: string; side: string } }) {
  return <div className="relative w-[220px] rounded-xl border border-sky-200 bg-white px-4 py-3 shadow-sm">{data.side === "target" && <Handle type="target" position={Position.Left} className="!h-2.5 !w-2.5 !border-2 !border-white !bg-sky-500" />}<p className="text-[10px] font-bold uppercase tracking-wide text-sky-700">{data.side} system</p><p className="mt-1 truncate text-sm font-bold text-slate-800" title={data.label}>{data.label}</p><p className="mt-2 text-[10px] text-slate-500">{data.side === "source" ? "Produces this data" : "Receives this data"}</p>{data.side === "source" && <Handle type="source" position={Position.Right} className="!h-2.5 !w-2.5 !border-2 !border-white !bg-sky-500" />}</div>;
}

function ArtifactNode({ data }: { data: { record: LandscapeProvenance; selected?: boolean } }) {
  const record = data.record;
  return <div className={`relative w-[280px] rounded-xl border bg-white px-4 py-3 shadow-sm transition ${data.selected ? "border-cyan-500 ring-4 ring-cyan-100" : "border-cyan-200"}`}><Handle type="target" position={Position.Left} className="!h-2.5 !w-2.5 !border-2 !border-white !bg-cyan-500" /><p className="text-[10px] font-bold uppercase tracking-wide text-cyan-700">Implementing design</p><p className="mt-1 line-clamp-2 text-sm font-bold text-slate-800">{record.artifact_id}</p><p className="mt-1 text-[10px] leading-snug text-slate-500">Select to inspect purpose and mappings.</p>{record.component_name && <p className="mt-1 truncate rounded bg-cyan-50 px-1.5 py-0.5 text-[9px] text-cyan-800">{record.component_name}</p>}<Handle type="source" position={Position.Right} className="!h-2.5 !w-2.5 !border-2 !border-white !bg-cyan-500" /></div>;
}
const nodeTypes = { integrationBoundary: memo(BoundaryNode), integrationArtifact: memo(ArtifactNode) };

function findingsForStream(landscape: ProjectLandscapeResponse, edge: TopologyEdge) {
  const stream = edge.businessStream;
  if (!stream || !landscape.business_topology) return { controls: [], risks: [], unknowns: [], conflicts: [], runtime: [] };
  const matches = (item: { stream_ids: string[] }) => item.stream_ids.includes(stream.id);
  return {
    controls: landscape.business_topology.controls.filter((item) => stream.control_ids.includes(item.id) || matches(item)),
    risks: landscape.business_topology.risks.filter((item) => stream.risk_ids.includes(item.id) || matches(item)),
    unknowns: landscape.business_topology.unknowns.filter((item) => stream.unknown_ids.includes(item.id) || matches(item)),
    conflicts: (landscape.business_topology.conflicts ?? []).filter((item) => stream.conflict_ids?.includes(item.id) || item.subject_refs.includes(stream.id)),
    runtime: (landscape.business_topology.runtime_observations ?? []).filter((item) => stream.runtime_observation_ids?.includes(item.id) || matches(item)),
  };
}

function DeliveryPanel({ landscape, edge }: { landscape: ProjectLandscapeResponse; edge: TopologyEdge }) {
  const stream = edge.businessStream;
  const path = stream?.delivery_path_id ? landscape.business_topology?.delivery_paths.find((item) => item.id === stream.delivery_path_id) : undefined;
  if (!path) return <Empty text="No ordered delivery path was established for this stream." />;
  return <section className="mx-auto max-w-4xl p-5"><h2 className="text-sm font-bold text-slate-800">Delivery path</h2><p className="mt-1 text-xs text-slate-500">Technical mechanisms are shown in order; they do not replace the scanner graph.</p><ol className="mt-4 space-y-2">{[...path.segments].sort((a, b) => a.sequence - b.sequence).map((segment) => <li key={segment.id} className="rounded-lg border bg-white p-3 text-xs"><p className="font-semibold text-slate-800">{segment.sequence}. {landscapeEntityLabel(landscape, segment.source_ref)} → {landscapeEntityLabel(landscape, segment.destination_ref)}</p><p className="mt-1 text-slate-600">{topologyTerm(segment.mechanism)} · {topologyTerm(segment.mode)}{segment.description ? ` · ${segment.description}` : ""}</p></li>)}</ol></section>;
}

function ControlsPanel({ landscape, edge }: { landscape: ProjectLandscapeResponse; edge: TopologyEdge }) {
  const findings = findingsForStream(landscape, edge);
  return <section className="mx-auto max-w-4xl p-5"><h2 className="text-sm font-bold text-slate-800">Controls, runtime, and open questions</h2><FindingList title="Controls" items={findings.controls} /><FindingList title="Risks" items={findings.risks} tone="amber" /><FindingList title="Runtime observations" items={findings.runtime.map((item) => ({ ...item, description: item.observed_behavior }))} tone="sky" /><FindingList title="Discrepancies" items={findings.conflicts} tone="red" /><FindingList title="Not established" items={findings.unknowns} tone="slate" /></section>;
}

function FindingList({ title, items, tone = "emerald" }: { title: string; items: Array<{ id: string; name: string; description: string; status: string }>; tone?: "emerald" | "amber" | "slate" | "sky" | "red" }) {
  const styles = tone === "amber" ? "border-amber-200 bg-amber-50" : tone === "slate" ? "border-slate-200 bg-slate-50" : tone === "sky" ? "border-sky-200 bg-sky-50" : tone === "red" ? "border-red-200 bg-red-50" : "border-emerald-200 bg-emerald-50";
  return <section className="mt-5"><h3 className="text-xs font-bold text-slate-700">{title}</h3>{items.length === 0 ? <p className="mt-2 text-xs text-slate-500">None established for this stream.</p> : <div className="mt-2 space-y-2">{items.map((item) => <div key={item.id} className={`rounded-lg border p-3 text-xs ${styles}`}><p className="font-semibold text-slate-800">{item.name || "Unnamed finding"}</p>{item.description && <p className="mt-1 text-slate-700">{item.description}</p>}</div>)}</div>}</section>;
}

function EndpointEvidence({ landscape, edge }: { landscape: ProjectLandscapeResponse; edge: TopologyEdge }) {
  const detail = integrationDetailForEdge(landscape, edge);
  if (detail.channels.length === 0 && detail.interfaces.length === 0) return <p className="mt-3 text-xs text-slate-500">No channel or endpoint evidence is attached to this stream.</p>;
  return <div className="mt-3 space-y-2">{detail.channels.map((item) => <div key={`channel:${item.qualified_artifact_id}:${item.channel}`} className="rounded border bg-white p-2 text-xs"><p className="font-semibold text-slate-700">Channel · {item.channel}</p><p className="mt-1 text-slate-500">{topologyTerm(item.channel_type)} · {topologyTerm(item.direction)} · {item.component_name || item.artifact_id}</p>{item.redaction_descriptor && <p className="mt-1 text-[10px] text-slate-500">Endpoint redacted; protocol context retained ({item.redaction_descriptor.scheme || "scheme unknown"}, {item.redaction_descriptor.tls === true ? "TLS" : item.redaction_descriptor.tls === false ? "no TLS" : "TLS not established"}).</p>}</div>)}{detail.interfaces.map((item) => <div key={`interface:${item.qualified_artifact_id}:${item.endpoint_type}`} className="rounded border bg-white p-2 text-xs"><p className="font-semibold text-slate-700">Endpoint · {topologyTerm(item.endpoint_type)}</p><p className="mt-1 text-slate-500">{topologyTerm(item.direction)} · {item.component_name || item.artifact_id}</p>{item.redaction_descriptor && <p className="mt-1 text-[10px] text-slate-500">Endpoint value redacted; structural descriptor retained ({item.redaction_descriptor.scheme || "scheme unknown"}).</p>}</div>)}</div>;
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
                  <div className="flex shrink-0 items-center gap-2"><span className="rounded bg-slate-100 px-2 py-1 text-[10px] font-semibold text-slate-600">{design.mappings.length} mapping{design.mappings.length === 1 ? "" : "s"}</span><ChevronRight className="h-4 w-4 text-slate-400" /></div>
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
            <div className="flex items-center justify-between gap-3"><h3 className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-wide text-slate-600"><Code2 className="h-3.5 w-3.5 text-cyan-600" /> Mappings</h3><span className="text-[10px] font-semibold text-slate-400">{selected.mappings.length}</span></div>
            {selected.mappings.length > 0 ? <div className="mt-3 space-y-2">{selected.mappings.map((mapping, index) => <div key={`${mapping.source}:${mapping.target}:${index}`} className="rounded-xl border border-slate-200 bg-slate-50/60 p-3"><p className="text-[9px] font-semibold uppercase tracking-wide text-slate-400">{mapping.group}</p><div className="mt-2 grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-2"><code className="break-all rounded bg-white px-2 py-1 text-[10px] text-slate-700">{mapping.source}</code><ChevronRight className="h-3.5 w-3.5 text-cyan-600" /><code className="break-all rounded bg-white px-2 py-1 text-[10px] text-slate-700">{mapping.target}</code></div><p className="mt-2 text-[9px] text-slate-400">{topologyTerm(mapping.mappingType)} · {mapping.origin === "scanner_lineage" ? "scanner lineage" : "structured transformation"}</p></div>)}</div> : <div className="mt-3 rounded-xl border border-dashed border-slate-200 p-3 text-xs text-slate-500">{selected.mappingAttribution === "ambiguous" ? "Mappings exist for a duplicate job name, but the available evidence cannot safely assign them to this source root." : selected.invokedDesigns.length > 0 ? "This route delegates processing to the design shown below. No field-level mapping was extracted for that path." : "No field or value mappings were established for this design."}</div>}
            {(selected.invokedDesigns.length > 0 || selected.mappingComponents.length > 0) && <div className="mt-3 rounded-xl bg-cyan-50/70 p-3 text-[10px]"><p className="font-bold uppercase tracking-wide text-cyan-800">Mapping implementation</p>{selected.invokedDesigns.length > 0 && <p className="mt-1 text-cyan-900"><b>Invokes:</b> {selected.invokedDesigns.join(", ")}</p>}{selected.mappingComponents.length > 0 && <p className="mt-1 text-cyan-900"><b>Relevant components:</b> {selected.mappingComponents.join(", ")}</p>}</div>}
          </section>
          <section><h3 className="text-[11px] font-bold uppercase tracking-wide text-slate-500">How it participates</h3><div className="mt-2 flex flex-wrap gap-1.5">{selected.integrationSolutions.map((item) => <DetailTag key={item}>{item}</DetailTag>)}{selected.protocols.map((item) => <DetailTag key={item}>{item}</DetailTag>)}{selected.channels.map((item) => <DetailTag key={`${item.channel}:${item.component_name}`}>{topologyTerm(item.channel_type)} · {item.channel}</DetailTag>)}{selected.interfaces.map((item) => <DetailTag key={`${item.endpoint_type}:${item.component_name}`}>{topologyTerm(item.endpoint_type)} endpoint</DetailTag>)}</div>{selected.deliverySegments.length > 0 && <ol className="mt-3 space-y-2">{selected.deliverySegments.map((segment) => <li key={segment.id} className="rounded-lg border border-slate-100 p-2 text-[10px]"><p className="font-semibold text-slate-700">{segment.name}</p><p className="mt-1 text-slate-500">{[segment.protocol, segment.component, segment.resource].filter(Boolean).join(" · ")}</p></li>)}</ol>}</section>
          <button type="button" onClick={() => onOpen(selected.record)} className="w-full rounded-lg bg-slate-900 px-3 py-2 text-xs font-semibold text-white hover:bg-slate-700">Open full technical design</button>
        </div>
      )}
    </aside>
  );
}

export function IntegrationDetailCanvas({ landscape, edge, onBack, onOpenArtifact }: { landscape: ProjectLandscapeResponse; edge: TopologyEdge; onBack: () => void; onOpenArtifact: (record: LandscapeProvenance) => void }) {
  const [tab, setTab] = useState<"flow" | "delivery" | "controls" | "evidence" | "designs">("flow");
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(null);
  const [explanationOpen, setExplanationOpen] = useState(true);
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
  useEffect(() => {
    setNodes(detail.nodes.map((node) => ({
      ...node,
      data: { ...node.data, selected: node.id === `artifact:${selectedArtifactId}` },
    })));
    setEdges(detail.edges);
  }, [detail, selectedArtifactId, setNodes, setEdges]);
  useEffect(() => {
    setSelectedArtifactId(null);
    setExplanationOpen(true);
  }, [edge]);
  const onDoubleClick: NodeMouseHandler = (_event, node) => { const record = (node.data as { record?: LandscapeProvenance }).record; if (record) onOpenArtifact(record); };
  const onNodeClick: NodeMouseHandler = (_event, node) => {
    const record = (node.data as { record?: LandscapeProvenance }).record;
    if (!record) return;
    setSelectedArtifactId(record.qualified_artifact_id);
    setExplanationOpen(true);
  };
  const implementations = stream?.integration_solution_ids.map((id) => landscape.business_topology?.integration_solutions.find((item) => item.id === id)?.name).filter(Boolean) ?? [];
  const content = tab === "delivery" ? <DeliveryPanel landscape={landscape} edge={edge} /> : tab === "controls" ? <ControlsPanel landscape={landscape} edge={edge} /> : tab === "evidence" ? <section className="mx-auto max-w-4xl p-5"><h2 className="flex items-center gap-2 text-sm font-bold text-slate-800"><Network className="h-4 w-4" /> Technical evidence</h2><ArtifactList provenance={detail.provenance} onOpen={onOpenArtifact} /><h3 className="mt-5 text-xs font-bold text-slate-700">Channels and endpoints</h3><EndpointEvidence landscape={landscape} edge={edge} />{detail.diagnostics.map((item, index) => <p key={index} className="mt-3 rounded border border-amber-100 bg-amber-50 p-2 text-xs text-amber-900"><FileWarning className="mr-1 inline h-3.5 w-3.5" />{item.description}</p>)}</section> : tab === "designs" ? <section className="p-5"><h2 className="text-sm font-bold text-slate-800">Implementing designs</h2><p className="mt-1 text-xs text-slate-500">Open a design to view the scanner-authored job or route graph.</p><ArtifactList provenance={detail.provenance} onOpen={onOpenArtifact} /></section> : null;
  return <div className="absolute inset-0" data-testid="integration-detail-canvas"><div className="absolute inset-x-0 top-0 z-20 border-b border-slate-200 bg-white/95 px-4 py-2 shadow-sm backdrop-blur"><div className="flex flex-wrap items-center gap-2"><button type="button" onClick={onBack} className="inline-flex items-center gap-1 rounded border border-slate-200 px-2 py-1 text-xs font-semibold text-slate-700 hover:bg-slate-50"><ArrowLeft className="h-3.5 w-3.5" /> Landscape</button><div className="min-w-0 flex-1"><p className="truncate text-sm font-bold text-slate-800">{sourceLabel} <span className="text-slate-400">→</span> {targetLabel}</p><p className="text-[10px] text-slate-500">L2 data stream · {stream?.name || topologyTerm(edge.dependency_type)}</p></div>{(["flow", "delivery", "controls", "evidence", "designs"] as const).map((item) => <button key={item} type="button" onClick={() => setTab(item)} className={`rounded px-2 py-1 text-[11px] font-semibold capitalize ${tab === item ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"}`}>{item}</button>)}{tab === "flow" && !explanationOpen && <button type="button" onClick={() => setExplanationOpen(true)} className="rounded border border-cyan-200 px-2 py-1 text-[11px] font-semibold text-cyan-700 hover:bg-cyan-50">Explain</button>}</div>{stream && <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 border-t border-slate-100 pt-2 text-[10px] text-slate-600"><span><b>Purpose:</b> {stream.business_summary || "Not established"}</span><span><b>Implemented by:</b> {implementations.join(", ") || "Not established"}</span></div>}</div><div className={tab === "flow" ? `absolute bottom-0 left-0 top-[102px] transition-[right] ${explanationOpen ? "right-[410px]" : "right-0"}` : "absolute inset-x-0 bottom-0 top-[102px] overflow-auto bg-slate-50"}>{tab === "flow" ? <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onNodeClick={onNodeClick} onNodeDoubleClick={onDoubleClick} fitView minZoom={0.1} maxZoom={3} panActivationKeyCode={null} onlyRenderVisibleElements proOptions={{ hideAttribution: true }}><Background variant={BackgroundVariant.Dots} gap={24} size={0.8} color="#cbd5e1" className="opacity-20" /><Controls showInteractive={false} /><MiniMap /></ReactFlow> : content}</div>{tab === "flow" && explanationOpen && <DesignExplanationPanel designs={designs} selected={selectedDesign} sourceLabel={sourceLabel} targetLabel={targetLabel} streamSummary={stream?.business_summary || ""} onSelect={(design) => setSelectedArtifactId(design.record.qualified_artifact_id)} onClose={() => setExplanationOpen(false)} onOpen={onOpenArtifact} />}</div>;
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

function ArtifactList({ provenance, onOpen }: { provenance: LandscapeProvenance[]; onOpen: (record: LandscapeProvenance) => void }) { return <section className="mt-4"><h3 className="flex items-center gap-2 text-xs font-bold text-slate-700"><Box className="h-4 w-4" /> Implementing artifacts</h3><div className="mt-2 space-y-2">{provenance.length === 0 ? <p className="text-xs text-slate-500">No scanner provenance is attached to this stream.</p> : provenance.map((record) => <div key={`${record.qualified_artifact_id}:${record.component_name}:${record.fact_type}`} className="flex items-center justify-between gap-3 rounded border bg-white p-2"><div className="min-w-0"><p className="truncate text-xs font-semibold text-slate-700">{record.artifact_id}</p><p className="truncate text-[10px] text-slate-500">{record.source_root}{record.component_name ? ` · ${record.component_name}` : ""}</p></div><button type="button" onClick={() => onOpen(record)} className="shrink-0 rounded border border-slate-200 px-2 py-1 text-[10px] font-semibold text-slate-700 hover:bg-slate-50">Open design</button></div>)}</div></section>; }
