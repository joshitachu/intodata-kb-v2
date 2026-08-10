"use client";

type ContextClaim = { id: string; name: string; description: string; status: string; confidence: number };

/** Additive context only; it cannot alter scanner nodes, edges, or mappings. */
export function BusinessContextBanner({ annotations, level }: { annotations: ContextClaim[]; level: "L3 job" | "L4 mapping" }) {
  if (annotations.length === 0) return null;
  return <aside className="absolute left-3 right-3 top-3 z-30 max-w-2xl rounded-lg border border-sky-200 bg-sky-50/95 px-3 py-2 shadow-sm"><p className="text-[10px] font-bold uppercase tracking-wide text-sky-700">{level} business context</p><div className="mt-0.5 space-y-1">{annotations.map((item) => <p key={item.id} className="text-xs text-sky-950"><span className="font-semibold">{item.name}:</span> {item.description || "No further explanation established."}</p>)}</div><p className="mt-1 text-[10px] text-sky-700">Explanation only — technical graph and lineage remain scanner-authored.</p></aside>;
}
