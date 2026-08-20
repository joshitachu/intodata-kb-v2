"use client";

import { useCallback, useMemo, useState } from "react";

// Breadcrumb navigation stack for the topology drill-down
// (Landscape > Integration/legacy Group > Job > Component).
// Each entry is one rendered React Flow canvas level.

export type IntegrationSelection =
  | { kind: "application"; applicationId: string }
  | { kind: "system"; systemId: string }
  | { kind: "edge"; edgeKey: string }
  | { kind: "relationship"; source: string; target: string; streamKeys: string[] };

export type TopologyLevel =
  | { kind: "project" }
  | { kind: "integration"; selection: IntegrationSelection }
  | { kind: "group"; groupName: string; sourceRoot?: string }
  | { kind: "job"; jobName: string; sourceRoot?: string }
  | { kind: "component"; jobName: string; componentName: string; sourceRoot?: string };

export interface TopologyNavigation {
  stack: TopologyLevel[];
  current: TopologyLevel;
  depth: number;
  descendToIntegration: (selection: IntegrationSelection) => void;
  descendToGroup: (groupName: string, sourceRoot?: string) => void;
  descendToJob: (jobName: string, sourceRoot?: string) => void;
  descendToComponent: (componentName: string) => void;
  ascend: () => void;
  jumpTo: (index: number) => void;
  reset: () => void;
}

const ROOT: TopologyLevel[] = [{ kind: "project" }];

export function useTopologyNavigation(): TopologyNavigation {
  const [stack, setStack] = useState<TopologyLevel[]>(ROOT);

  const descendToIntegration = useCallback((selection: IntegrationSelection) => {
    setStack((prev) => {
      const top = prev[prev.length - 1];
      if (top.kind === "integration" && JSON.stringify(top.selection) === JSON.stringify(selection)) return prev;
      return [{ kind: "project" }, { kind: "integration", selection }];
    });
  }, []);

  const descendToGroup = useCallback((groupName: string, sourceRoot?: string) => {
    setStack((prev) => {
      const top = prev[prev.length - 1];
      if (top.kind === "group" && top.groupName === groupName && top.sourceRoot === sourceRoot) return prev;
      // A group is always a direct child of the landscape. Replacing any
      // deeper path avoids stale Job/Component crumbs when switching groups.
      return [{ kind: "project" }, { kind: "group", groupName, sourceRoot }];
    });
  }, []);

  const descendToJob = useCallback((jobName: string, sourceRoot?: string) => {
    setStack((prev) => {
      const top = prev[prev.length - 1];
      // Re-descending into the level already on top is a no-op
      // (e.g. a tRunJob portal pointing at the job currently shown).
      if (top.kind === "job" && top.jobName === jobName && top.sourceRoot === sourceRoot) return prev;
      return [...prev, { kind: "job", jobName, sourceRoot }];
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
      const sourceRoot =
        top.kind === "job" || top.kind === "component" ? top.sourceRoot : undefined;
      if (jobName === null) return prev;
      if (top.kind === "component" && top.componentName === componentName) {
        return prev;
      }
      return [...prev, { kind: "component", jobName, componentName, sourceRoot }];
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
      descendToIntegration,
      descendToGroup,
      descendToJob,
      descendToComponent,
      ascend,
      jumpTo,
      reset,
    }),
    [
      stack,
      current,
      depth,
      descendToIntegration,
      descendToGroup,
      descendToJob,
      descendToComponent,
      ascend,
      jumpTo,
      reset,
    ],
  );
}
