"use client";

import { useCallback, useMemo, useState } from "react";

// Breadcrumb navigation stack for the topology drill-down
// (Project > Job > Component - S2 adds the component level's table graph).
// Each entry is one rendered React Flow canvas level.

export type TopologyLevel =
  | { kind: "project" }
  | { kind: "job"; jobName: string }
  | { kind: "component"; jobName: string; componentName: string };

export interface TopologyNavigation {
  stack: TopologyLevel[];
  current: TopologyLevel;
  depth: number;
  descendToJob: (jobName: string) => void;
  descendToComponent: (componentName: string) => void;
  ascend: () => void;
  jumpTo: (index: number) => void;
  reset: () => void;
}

const ROOT: TopologyLevel[] = [{ kind: "project" }];

export function useTopologyNavigation(): TopologyNavigation {
  const [stack, setStack] = useState<TopologyLevel[]>(ROOT);

  const descendToJob = useCallback((jobName: string) => {
    setStack((prev) => {
      const top = prev[prev.length - 1];
      // Re-descending into the level already on top is a no-op
      // (e.g. a tRunJob portal pointing at the job currently shown).
      if (top.kind === "job" && top.jobName === jobName) return prev;
      return [...prev, { kind: "job", jobName }];
    });
  }, []);

  // Descends from a job level (or an adjacent component level, same job)
  // into that job's level-3 table graph for one component. Only called for
  // components with has_subgraph=true (tMap) - see jobGraphTransform's
  // classifyComponentDoubleClick.
  const descendToComponent = useCallback((componentName: string) => {
    setStack((prev) => {
      const top = prev[prev.length - 1];
      const jobName =
        top.kind === "job" || top.kind === "component" ? top.jobName : null;
      if (jobName === null) return prev;
      if (top.kind === "component" && top.componentName === componentName) {
        return prev;
      }
      return [...prev, { kind: "component", jobName, componentName }];
    });
  }, []);

  const ascend = useCallback(() => {
    setStack((prev) => (prev.length > 1 ? prev.slice(0, -1) : prev));
  }, []);

  const jumpTo = useCallback((index: number) => {
    setStack((prev) =>
      index >= 0 && index < prev.length - 1 ? prev.slice(0, index + 1) : prev,
    );
  }, []);

  const reset = useCallback(() => {
    setStack(ROOT);
  }, []);

  const current = stack[stack.length - 1];
  const depth = stack.length;

  return useMemo(
    () => ({
      stack,
      current,
      depth,
      descendToJob,
      descendToComponent,
      ascend,
      jumpTo,
      reset,
    }),
    [stack, current, depth, descendToJob, descendToComponent, ascend, jumpTo, reset],
  );
}
