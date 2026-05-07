import { useQualityHistory } from "../hooks/useProjects";

interface Props {
  projectId: string;
}

function formatQuality(value: number | null) {
  return value == null ? "-" : value.toFixed(2);
}

export default function QualityHistoryPanel({ projectId }: Props) {
  const { data, isLoading, isError } = useQualityHistory(projectId);
  const history = data?.history ?? [];

  return (
    <div className="card-dark">
      <div className="px-4 py-3 border-b border-white/[0.05] bg-surface-raised rounded-t">
        <h3 className="text-sm font-mono text-slate-400">Quality History</h3>
      </div>
      <div className="p-4">
        {isLoading ? (
          <p className="text-sm text-slate-500">Loading quality history...</p>
        ) : isError ? (
          <p className="text-sm text-red-400">Failed to load quality history</p>
        ) : history.length === 0 ? (
          <p className="text-sm text-slate-500">No scored runs yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/[0.04] text-left font-mono text-xs text-slate-600 uppercase">
                  <th className="px-3 py-2 font-medium">Run</th>
                  <th className="px-3 py-2 font-medium">Quality</th>
                  <th className="px-3 py-2 font-medium">Delta</th>
                  <th className="px-3 py-2 font-medium">Models</th>
                  <th className="px-3 py-2 font-medium">Cost</th>
                </tr>
              </thead>
              <tbody>
                {history.map((entry) => (
                  <tr key={entry.run_id ?? entry.started_at} className="border-b border-white/[0.04]">
                    <td className="px-3 py-2 font-mono text-slate-400">#{entry.run_id ?? "?"}</td>
                    <td className="px-3 py-2">
                      <span className={entry.regression ? "text-red-400" : "text-[#a0e06f]"}>
                        {formatQuality(entry.quality)}
                      </span>
                    </td>
                    <td className="px-3 py-2 font-mono text-slate-500">
                      {entry.quality_delta_pct == null
                        ? "-"
                        : `${entry.quality_delta_pct > 0 ? "+" : ""}${entry.quality_delta_pct.toFixed(2)}%`}
                      {entry.regression && <span className="ml-2 text-red-400">REGRESSION</span>}
                    </td>
                    <td className="px-3 py-2 text-xs text-slate-500">
                      <div className="font-mono">{entry.analysis_model ?? "-"}</div>
                      <div className="font-mono text-slate-600">{entry.write_model ?? "-"}</div>
                    </td>
                    <td className="px-3 py-2 font-mono text-slate-500">
                      {entry.cost_usd == null ? "-" : `$${entry.cost_usd.toFixed(3)}`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
