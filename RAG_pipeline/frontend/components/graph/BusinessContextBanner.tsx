"use client";

/**
 * The shared claim fields plus whichever explanation slot the annotation kind
 * carries. Structural, so artifact, component and lineage annotations pass
 * straight through without a per-kind adapter.
 */
type ContextClaim = {
  id: string;
  name: string;
  description: string;
  status: string;
  confidence: number;
  /** ArtifactAnnotation */
  business_role?: string;
  /** ComponentAnnotation */
  human_explanation?: string;
  /** LineageAnnotation */
  business_meaning?: string;
};

/** The annotation's own explanation, falling back to its description. */
function explanation(item: ContextClaim): string {
  return item.human_explanation || item.business_meaning || item.description;
}

/** Additive context only; it cannot alter scanner nodes, edges, or mappings. */
export function BusinessContextBanner({ annotations, level }: { annotations: ContextClaim[]; level: "L3 job" | "L4 mapping" }) {
  if (annotations.length === 0) return null;
  return <aside className="absolute left-3 right-3 top-3 z-30 max-w-2xl rounded-lg border border-sky-200 bg-sky-50/95 px-3 py-2 shadow-sm"><p className="text-[10px] font-bold uppercase tracking-wide text-sky-700">{level} business context</p><div className="mt-0.5 space-y-1">{annotations.map((item) => {
    const text = explanation(item);
    return <p key={item.id} className="text-xs text-sky-950"><span className="font-semibold">{item.name}:</span> {item.business_role && <span className="mr-1 rounded bg-sky-100 px-1 py-0.5 text-[10px] font-semibold text-sky-800">{item.business_role}</span>}{text || (item.business_role ? "" : "No further explanation established.")}{text && item.description && text !== item.description && <span className="mt-0.5 block text-[11px] text-sky-800">{item.description}</span>}</p>;
  })}</div><p className="mt-1 text-[10px] text-sky-700">Explanation only — technical graph and lineage remain scanner-authored.</p></aside>;
}
