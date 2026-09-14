import { useState, useEffect, useRef, useCallback } from "react";
import type { Project } from "../types";
import { timeAgo, formatAbsoluteDate } from "../lib/timeago";
import { UNKNOWN_DISPLAY, type Loadable } from "../lib/loadable";

interface Props {
  /**
   * The projects query's state, NOT a bare array.
   *
   * These tiles are aggregates over the project list, and a plain `Project[]`
   * prop made an unresolved query indistinguishable from an empty estate:
   * `reduce` over `[]` is 0 and `timeAgo(null)` is "Never", so a cold load
   * showed a confident "0 projects, 0 components, 0 documents, last activity
   * Never" and then replaced it with the real numbers. Threading the query
   * state means the tiles can say "not known yet" instead of guessing zero.
   */
  projects: Loadable<Project[]>;
}

/**
 * Animate 0 -> target once the target is known.
 *
 * `null` means "not known yet" and is passed straight through, so the caller
 * renders a placeholder. A known 0 still snaps in without animation -- there is
 * nothing to count up to -- but it is now only reachable from a successful
 * response.
 */
function useCountUp(target: number | null, duration = 800): number | null {
  const [value, setValue] = useState<number | null>(null);
  const rafRef = useRef(0);

  const easeOutQuart = useCallback((t: number) => 1 - Math.pow(1 - t, 4), []);

  useEffect(() => {
    if (target === null) { setValue(null); return; }
    if (target === 0) { setValue(0); return; }
    const start = performance.now();
    function tick(now: number) {
      const elapsed = now - start;
      const progress = Math.min(elapsed / duration, 1);
      setValue(Math.round(easeOutQuart(progress) * target!));
      if (progress < 1) rafRef.current = requestAnimationFrame(tick);
    }
    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
  }, [target, duration, easeOutQuart]);

  // The effect runs after paint, so a target that has just become known would
  // otherwise render as unknown for one frame -- a placeholder flashing between
  // the real data arriving and the animation starting. Known target, no tick
  // yet, means "start of the count-up", which is 0.
  return target === null ? null : value ?? 0;
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
  /** `null` means not known yet; it renders as the placeholder, never as 0. */
  numericValue: number | null;
  /** Pre-formatted text. `null` carries the same meaning as above. */
  displayValue?: string | null;
  color: string;
  tooltip?: string;
  seed: number;
}

export default function KpiBar({ projects }: Props) {
  // The single place the unknown/known split is made. Every figure below is
  // derived from `rows`, so none of them can accidentally report a default.
  const rows = projects.status === "ready" ? projects.value : null;
  const isLoading = projects.status === "loading";

  const totalJobs = rows ? rows.reduce((sum, p) => sum + p.job_count, 0) : null;
  const totalDocs = rows ? rows.reduce((sum, p) => sum + p.doc_count, 0) : null;

  // Null from a successful response genuinely means "no activity recorded", and
  // "Never" is the right answer. Null because nothing has loaded is not.
  const latestTimestamp =
    rows?.reduce<string | null>((latest, p) => {
      const ts = p.last_scan_at ?? p.last_generate_at ?? p.updated_at;
      if (!ts) return latest;
      if (!latest) return ts;
      return ts > latest ? ts : latest;
    }, null) ?? null;

  const projectCount = useCountUp(rows ? rows.length : null);
  const jobCount = useCountUp(totalJobs);
  const docCount = useCountUp(totalDocs);

  const kpis: KpiItem[] = [
    { label: "Projects", numericValue: projectCount, color: "text-slate-300", seed: 42 },
    { label: "Components", numericValue: jobCount, color: "text-accent-primary", seed: 137 },
    { label: "Documents", numericValue: docCount, color: "text-[#a0e06f]", seed: 256 },
    {
      label: "Last Activity",
      numericValue: null,
      displayValue: rows ? timeAgo(latestTimestamp) : null,
      color: "text-[#6370b0]",
      tooltip: rows ? formatAbsoluteDate(latestTimestamp) : undefined,
      seed: 512,
    },
  ];

  const unknownTitle = isLoading ? "Loading..." : "Unavailable";

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
      {kpis.map((kpi) => {
        const text = kpi.displayValue ?? kpi.numericValue?.toLocaleString() ?? null;
        return (
          <div
            key={kpi.label}
            className="card-dark relative overflow-hidden px-5 py-4"
            title={text === null ? unknownTitle : kpi.tooltip}
            aria-busy={text === null && isLoading}
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

            <div
              className={`kpi-number relative ${
                text === null ? "text-slate-600" : kpi.color
              } ${text === null && isLoading ? "animate-pulse" : ""}`}
            >
              {text ?? UNKNOWN_DISPLAY}
            </div>
            <div className="text-xs font-medium text-slate-500 mt-1 relative">{kpi.label}</div>
          </div>
        );
      })}
    </div>
  );
}
