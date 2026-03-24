import { useVersions, useDiff } from "../../hooks/useTemplates";
import { useState } from "react";

interface Props {
  slug: string;
}

export default function VersionTimeline({ slug }: Props) {
  const { data, isLoading } = useVersions(slug);
  const versions = data?.versions ?? [];

  const [diffFrom, setDiffFrom] = useState<number | null>(null);
  const [diffTo, setDiffTo] = useState<number | null>(null);

  const { data: diffData, isLoading: diffLoading } = useDiff(slug, diffFrom, diffTo);

  if (isLoading) return <div className="p-4 text-sm text-slate-500">Loading versions...</div>;
  if (versions.length === 0) return null;

  return (
    <div className="card-dark">
      <div className="px-4 py-3 border-b border-white/[0.05] bg-surface-raised rounded-t">
        <h3 className="text-sm font-mono text-slate-400">
          Version History
          <span className="ml-2 text-xs text-slate-600">({versions.length})</span>
        </h3>
      </div>

      {/* Diff controls */}
      {versions.length >= 2 && (
        <div className="px-4 py-2 border-b border-white/[0.04] flex items-center gap-2 text-xs">
          <span className="text-slate-500">Diff:</span>
          <select
            value={diffFrom ?? ""}
            onChange={(e) => setDiffFrom(e.target.value ? Number(e.target.value) : null)}
            className="input-dark !w-auto text-xs !py-1"
          >
            <option value="">from...</option>
            {versions.map((v) => (
              <option key={v.version_number} value={v.version_number}>
                v{v.version_number}
              </option>
            ))}
          </select>
          <span className="text-slate-600">-&gt;</span>
          <select
            value={diffTo ?? ""}
            onChange={(e) => setDiffTo(e.target.value ? Number(e.target.value) : null)}
            className="input-dark !w-auto text-xs !py-1"
          >
            <option value="">to...</option>
            {versions.map((v) => (
              <option key={v.version_number} value={v.version_number}>
                v{v.version_number}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* Diff display */}
      {diffData && (
        <div className="px-4 py-2 border-b border-white/[0.04]">
          {diffLoading ? (
            <span className="text-xs text-slate-500">Loading diff...</span>
          ) : (
            <pre className="text-xs font-mono overflow-x-auto max-h-[300px] overflow-y-auto">
              {diffData.diff.split("\n").map((line, i) => {
                let cls = "text-slate-500";
                if (line.startsWith("+")) cls = "text-[#a0e06f]";
                else if (line.startsWith("-")) cls = "text-red-400";
                else if (line.startsWith("@@")) cls = "text-accent-primary";
                return (
                  <div key={i} className={cls}>
                    {line}
                  </div>
                );
              })}
            </pre>
          )}
        </div>
      )}

      {/* Timeline */}
      <div className="divide-y divide-white/[0.04]">
        {versions.map((v) => (
          <div key={v.version_number} className="px-4 py-2.5 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <span className="text-xs font-mono text-accent-primary/60 w-8">v{v.version_number}</span>
              <div>
                <p className="text-sm text-slate-400">{v.change_summary}</p>
                <p className="text-xs text-slate-600">
                  {v.created_by ?? "unknown"}
                  {v.created_at && ` -- ${new Date(v.created_at).toLocaleString()}`}
                </p>
              </div>
            </div>
            <div className="text-xs text-slate-600 font-mono">
              {v.byte_size != null && `${(v.byte_size / 1024).toFixed(1)}KB`}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
