import type { ProjectUsage } from "../types";

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

interface Props {
  isLoading: boolean;
  isError: boolean;
  data: ProjectUsage | undefined;
}

export default function ProjectUsagePanel({ isLoading, isError, data }: Props) {
  return (
    <div>
      <h4 className="font-mono uppercase tracking-wider text-xs text-slate-600 mb-3">Usage Summary</h4>
      {isLoading ? (
        <p className="text-xs text-slate-500">Loading usage data...</p>
      ) : isError ? (
        <p className="text-xs text-red-400">Failed to load usage data</p>
      ) : data ? (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <div className="bg-surface-raised rounded px-3 py-2.5 border border-white/[0.07]">
            <div className="text-lg font-mono font-medium text-slate-300">
              {data.completed_generate_runs}/{data.total_generate_runs}
            </div>
            <div className="text-xs text-slate-500">Generate Runs</div>
          </div>
          <div className="bg-surface-raised rounded px-3 py-2.5 border border-white/[0.07]">
            <div className="text-lg font-mono font-medium text-slate-300">
              {data.completed_scan_runs}/{data.total_scan_runs}
            </div>
            <div className="text-xs text-slate-500">Scan Runs</div>
          </div>
          <div className="bg-surface-raised rounded px-3 py-2.5 border border-white/[0.07]">
            <div className="text-lg font-mono font-medium text-accent-primary">
              {formatTokens(data.total_input_tokens + data.total_output_tokens)}
            </div>
            <div className="text-xs text-slate-500">
              Total Tokens
              <span className="block text-slate-600">
                {formatTokens(data.total_input_tokens)} in / {formatTokens(data.total_output_tokens)} out
              </span>
            </div>
          </div>
          <div className="bg-surface-raised rounded px-3 py-2.5 border border-white/[0.07]">
            <div className="text-lg font-mono font-medium text-[#a0e06f]">
              ${data.total_cost_usd.toFixed(2)}
            </div>
            <div className="text-xs text-slate-500">Total Cost</div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
