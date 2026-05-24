import type { InstructionPackMetadata } from "../types";

interface Props {
  isLoading: boolean;
  isError: boolean;
  data: InstructionPackMetadata | undefined;
}

export default function InstructionPackPanel({ isLoading, isError, data }: Props) {
  return (
    <div>
      <h4 className="font-mono uppercase tracking-wider text-xs text-slate-600 mb-3">Instruction Pack</h4>
      {isLoading ? (
        <p className="text-xs text-slate-500">Loading instruction pack metadata...</p>
      ) : isError ? (
        <p className="text-xs text-red-400">Failed to load instruction pack metadata</p>
      ) : data ? (
        <div className="space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div className="bg-surface-raised rounded px-3 py-2.5 border border-white/[0.07]">
              <div className="text-sm font-mono text-slate-300 truncate" title={data.version}>
                {data.version}
              </div>
              <div className="text-xs text-slate-500">Version</div>
            </div>
            <div className="bg-surface-raised rounded px-3 py-2.5 border border-white/[0.07] sm:col-span-2">
              <div className="text-sm font-mono text-slate-300 truncate" title={data.path}>
                {data.relative_path}
              </div>
              <div className="text-xs text-slate-500">
                {data.supported ? "Local pack loaded" : "Pack unavailable"}
              </div>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {data.preambles.length === 0 ? (
              <span className="text-xs text-slate-600">No preambles found.</span>
            ) : (
              data.preambles.map((preamble) => (
                <span
                  key={preamble.key}
                  className="px-2 py-1 rounded bg-white/[0.04] border border-white/[0.06] text-xs font-mono text-slate-500"
                >
                  {preamble.key} ({preamble.chars} chars)
                </span>
              ))
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}
