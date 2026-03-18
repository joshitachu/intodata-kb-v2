import { useState, useEffect, useRef, useCallback } from "react";
import type { Project } from "../types";
import { timeAgo, formatAbsoluteDate } from "../lib/timeago";

interface Props {
  projects: Project[];
}

function useCountUp(target: number, duration = 800): number {
  const [value, setValue] = useState(0);
  const rafRef = useRef(0);

  const easeOutQuart = useCallback((t: number) => 1 - Math.pow(1 - t, 4), []);

  useEffect(() => {
    if (target === 0) { setValue(0); return; }
    const start = performance.now();
    function tick(now: number) {
      const elapsed = now - start;
      const progress = Math.min(elapsed / duration, 1);
      setValue(Math.round(easeOutQuart(progress) * target));
      if (progress < 1) rafRef.current = requestAnimationFrame(tick);
    }
    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
  }, [target, duration, easeOutQuart]);

  return value;
}

function generateSparkline(seed: number, width: number, height: number): string {
  const points = 12;
  const coords: string[] = [];
  let hash = seed;
  for (let i = 0; i < points; i++) {
    hash = ((hash * 1103515245 + 12345) & 0x7fffffff) >>> 0;
    const x = (i / (points - 1)) * width;
    const y = height * 0.2 + (hash % 100) / 100 * height * 0.6;
    coords.push(`${x.toFixed(1)},${y.toFixed(1)}`);
  }
  return coords.join(" ");
}

interface KpiItem {
  label: string;
  numericValue: number;
  displayValue?: string;
  color: string;
  tooltip?: string;
  seed: number;
}

export default function KpiBar({ projects }: Props) {
  const totalJobs = projects.reduce((sum, p) => sum + p.job_count, 0);
  const totalDocs = projects.reduce((sum, p) => sum + p.doc_count, 0);

  const latestTimestamp = projects.reduce<string | null>((latest, p) => {
    const ts = p.last_scan_at ?? p.last_generate_at ?? p.updated_at;
    if (!ts) return latest;
    if (!latest) return ts;
    return ts > latest ? ts : latest;
  }, null);

  const projectCount = useCountUp(projects.length);
  const jobCount = useCountUp(totalJobs);
  const docCount = useCountUp(totalDocs);

  const kpis: KpiItem[] = [
    { label: "Projects", numericValue: projectCount, color: "text-slate-300", seed: 42 },
    { label: "Components", numericValue: jobCount, color: "text-[#00D4FF]", seed: 137 },
    { label: "Documents", numericValue: docCount, color: "text-emerald-400", seed: 256 },
    {
      label: "Last Activity",
      numericValue: 0,
      displayValue: timeAgo(latestTimestamp),
      color: "text-violet-400",
      tooltip: formatAbsoluteDate(latestTimestamp),
      seed: 512,
    },
  ];

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
      {kpis.map((kpi) => (
        <div
          key={kpi.label}
          className="card-dark relative overflow-hidden px-5 py-4"
          title={kpi.tooltip}
        >
          {/* Sparkline background */}
          <svg
            className="absolute inset-0 w-full h-full opacity-[0.07]"
            viewBox="0 0 200 80"
            preserveAspectRatio="none"
          >
            <polyline
              points={generateSparkline(kpi.seed, 200, 80)}
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              className={kpi.color}
            />
          </svg>

          <div className={`kpi-number relative ${kpi.color}`}>
            {kpi.displayValue ?? kpi.numericValue.toLocaleString()}
          </div>
          <div className="text-xs font-medium text-slate-500 mt-1 relative">{kpi.label}</div>
        </div>
      ))}
    </div>
  );
}
