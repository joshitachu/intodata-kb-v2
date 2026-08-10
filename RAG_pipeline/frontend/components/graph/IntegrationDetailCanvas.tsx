"use client";

import { memo, useEffect, useMemo, useState } from "react";
import { ArrowLeft, Box, FileWarning, Network } from "lucide-react";
import { Background, BackgroundVariant, Controls, MiniMap, ReactFlow, type NodeMouseHandler, useEdgesState, useNodesState } from "@xyflow/react";
import { integrationDetailForEdge } from "@/lib/integrationDetailTransform";
import type { LandscapeProvenance, ProjectLandscapeResponse } from "@/types/landscape";
import { connectedEdges, landscapeEntityLabel, topologyTerm, type TopologyEdge } from "@/lib/landscapeTransform";

function BoundaryNode({ data }: { data: { label: string; side: string } }) {
  return <div className="w-[220px] rounded-xl border border-sky-200 bg-white px-4 py-3 shadow-sm"><p className="text-[10px] font-bold uppercase tracking-wide text-sky-700">{data.side} system</p><p className="mt-1 truncate text-sm font-bold text-slate-800" title={data.label}>{data.label}</p><p className="mt-2 text-[10px] text-slate-500">{data.side === "source" ? "Produces this data" : "Receives this data"}</p></div>;
}

function ArtifactNode({ data }: { data: { record: LandscapeProvenance } }) {
  const record = data.record;
  return <div className="w-[280px] rounded-xl border border-cyan-200 bg-white px-4 py-3 shadow-sm"><p className="text-[10px] font-bold uppercase tracking-wide text-cyan-700">Implementing design</p><p className="mt-1 line-clamp-2 text-sm font-bold text-slate-800">{record.artifact_id}</p><p className="mt-1 text-[10px] leading-snug text-slate-500">Scanner evidence implementing this data stream.</p>{record.component_name && <p className="mt-1 truncate rounded bg-cyan-50 px-1.5 py-0.5 text-[9px] text-cyan-800">{record.component_name}</p>}</div>;
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

export function IntegrationDetailCanvas({ landscape, edge, onBack, onOpenArtifact }: { landscape: ProjectLandscapeResponse; edge: TopologyEdge; onBack: () => void; onOpenArtifact: (record: LandscapeProvenance) => void }) {
  const [tab, setTab] = useState<"flow" | "delivery" | "controls" | "evidence" | "designs">("flow");
  const detail = useMemo(() => integrationDetailForEdge(landscape, edge), [landscape, edge]);
  const [nodes, setNodes, onNodesChange] = useNodesState(detail.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(detail.edges);
  const sourceLabel = landscapeEntityLabel(landscape, edge.source);
  const targetLabel = landscapeEntityLabel(landscape, edge.target);
  const stream = edge.businessStream;
  useEffect(() => { setNodes(detail.nodes); setEdges(detail.edges); }, [detail, setNodes, setEdges]);
  const onDoubleClick: NodeMouseHandler = (_event, node) => { const record = (node.data as { record?: LandscapeProvenance }).record; if (record) onOpenArtifact(record); };
  const implementations = stream?.integration_solution_ids.map((id) => landscape.business_topology?.integration_solutions.find((item) => item.id === id)?.name).filter(Boolean) ?? [];
  const content = tab === "delivery" ? <DeliveryPanel landscape={landscape} edge={edge} /> : tab === "controls" ? <ControlsPanel landscape={landscape} edge={edge} /> : tab === "evidence" ? <section className="mx-auto max-w-4xl p-5"><h2 className="flex items-center gap-2 text-sm font-bold text-slate-800"><Network className="h-4 w-4" /> Technical evidence</h2><ArtifactList provenance={detail.provenance} onOpen={onOpenArtifact} /><h3 className="mt-5 text-xs font-bold text-slate-700">Channels and endpoints</h3><EndpointEvidence landscape={landscape} edge={edge} />{detail.diagnostics.map((item, index) => <p key={index} className="mt-3 rounded border border-amber-100 bg-amber-50 p-2 text-xs text-amber-900"><FileWarning className="mr-1 inline h-3.5 w-3.5" />{item.description}</p>)}</section> : tab === "designs" ? <section className="p-5"><h2 className="text-sm font-bold text-slate-800">Implementing designs</h2><p className="mt-1 text-xs text-slate-500">Open a design to view the scanner-authored job or route graph.</p><ArtifactList provenance={detail.provenance} onOpen={onOpenArtifact} /></section> : null;
  return <div className="absolute inset-0" data-testid="integration-detail-canvas"><div className="absolute inset-x-0 top-0 z-20 border-b border-slate-200 bg-white/95 px-4 py-2 shadow-sm backdrop-blur"><div className="flex flex-wrap items-center gap-2"><button type="button" onClick={onBack} className="inline-flex items-center gap-1 rounded border border-slate-200 px-2 py-1 text-xs font-semibold text-slate-700 hover:bg-slate-50"><ArrowLeft className="h-3.5 w-3.5" /> Landscape</button><div className="min-w-0 flex-1"><p className="truncate text-sm font-bold text-slate-800">{sourceLabel} <span className="text-slate-400">→</span> {targetLabel}</p><p className="text-[10px] text-slate-500">L2 data stream · {stream?.name || topologyTerm(edge.dependency_type)}</p></div>{(["flow", "delivery", "controls", "evidence", "designs"] as const).map((item) => <button key={item} type="button" onClick={() => setTab(item)} className={`rounded px-2 py-1 text-[11px] font-semibold capitalize ${tab === item ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"}`}>{item}</button>)}</div>{stream && <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 border-t border-slate-100 pt-2 text-[10px] text-slate-600"><span><b>Purpose:</b> {stream.business_summary || "Not established"}</span><span><b>Implemented by:</b> {implementations.join(", ") || "Not established"}</span></div>}</div><div className={tab === "flow" ? "absolute inset-x-0 bottom-0 top-[102px]" : "absolute inset-x-0 bottom-0 top-[102px] overflow-auto bg-slate-50"}>{tab === "flow" ? <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onNodeDoubleClick={onDoubleClick} fitView minZoom={0.1} maxZoom={3} panActivationKeyCode={null} onlyRenderVisibleElements proOptions={{ hideAttribution: true }}><Background variant={BackgroundVariant.Dots} gap={24} size={0.8} color="#cbd5e1" className="opacity-20" /><Controls showInteractive={false} /><MiniMap /></ReactFlow> : content}</div></div>;
}

export function IntegrationRelationshipChooser({ landscape, entityId, title, onBack, onChoose }: { landscape: ProjectLandscapeResponse; entityId: string; title: string; onBack: () => void; onChoose: (edge: TopologyEdge) => void }) {
  const relationships = connectedEdges(landscape, entityId);
  return <div className="absolute inset-0 overflow-auto bg-slate-50 p-4"><div className="mx-auto max-w-3xl"><button type="button" onClick={onBack} className="inline-flex items-center gap-1 rounded border border-slate-200 bg-white px-2 py-1 text-xs font-semibold text-slate-700 hover:bg-slate-50"><ArrowLeft className="h-3.5 w-3.5" /> Landscape</button><h2 className="mt-4 text-lg font-bold text-slate-800">Explore data streams for {title}</h2><p className="mt-1 text-sm text-slate-500">Choose a stream to see how it is delivered and which designs implement it.</p><div className="mt-4 space-y-2">{relationships.length === 0 ? <Empty text="No data streams are available for this system." /> : relationships.map((edge) => <button key={edge.businessStream?.id || `${edge.source}:${edge.target}:${edge.mechanism}`} type="button" onClick={() => onChoose(edge)} className="w-full rounded-xl border bg-white p-4 text-left shadow-sm transition hover:border-blue-300 hover:bg-blue-50/30"><p className="font-semibold text-slate-800">{landscapeEntityLabel(landscape, edge.source)} <span className="text-slate-400">→</span> {landscapeEntityLabel(landscape, edge.target)}</p><p className="mt-1 text-sm font-medium text-sky-800">{edge.businessStream?.name || topologyTerm(edge.dependency_type)}</p><p className="mt-1 text-xs text-slate-500">{edge.businessStream?.business_summary || topologyTerm(edge.mechanism)}</p></button>)}</div></div></div>;
}

function ArtifactList({ provenance, onOpen }: { provenance: LandscapeProvenance[]; onOpen: (record: LandscapeProvenance) => void }) { return <section className="mt-4"><h3 className="flex items-center gap-2 text-xs font-bold text-slate-700"><Box className="h-4 w-4" /> Implementing artifacts</h3><div className="mt-2 space-y-2">{provenance.length === 0 ? <p className="text-xs text-slate-500">No scanner provenance is attached to this stream.</p> : provenance.map((record) => <div key={`${record.qualified_artifact_id}:${record.component_name}:${record.fact_type}`} className="flex items-center justify-between gap-3 rounded border bg-white p-2"><div className="min-w-0"><p className="truncate text-xs font-semibold text-slate-700">{record.artifact_id}</p><p className="truncate text-[10px] text-slate-500">{record.source_root}{record.component_name ? ` · ${record.component_name}` : ""}</p></div><button type="button" onClick={() => onOpen(record)} className="shrink-0 rounded border border-slate-200 px-2 py-1 text-[10px] font-semibold text-slate-700 hover:bg-slate-50">Open design</button></div>)}</div></section>; }
