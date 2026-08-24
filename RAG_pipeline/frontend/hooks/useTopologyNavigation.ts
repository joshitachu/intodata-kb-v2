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
  /**
   * Land directly on `level`, as if the user had drilled there by hand: the
   * stack becomes the canonical path to it, so the breadcrumb reads normally
   * and `ascend` walks back out one level at a time.
   */
  restore: (level: TopologyLevel) => void;
  /**
   * Put an exact stack back. Used to undo a jump the ANSWER made rather than
   * the user (see the auto-navigation path in `app/topology/page.tsx`): a
   * component jump grows the stack by two, so a single `ascend` would leave
   * the user somewhere they never asked to be.
   */
  replaceStack: (stack: readonly TopologyLevel[]) => void;
}

const ROOT: TopologyLevel[] = [{ kind: "project" }];

/**
 * The canonical stack for a level -- what the breadcrumb would show had the
 * user drilled there themselves.
 *
 * A job is treated as a direct child of the landscape rather than of a group:
 * the level itself does not name a group (only an optional source root), so
 * inventing an intermediate crumb would mean guessing. `component` keeps its
 * job crumb because the level carries the job name.
 */
export function topologyStackForLevel(level: TopologyLevel): TopologyLevel[] {
  switch (level.kind) {
    case "project":
      return [{ kind: "project" }];
    case "component":
      return [
        { kind: "project" },
        { kind: "job", jobName: level.jobName, sourceRoot: level.sourceRoot },
        level,
      ];
    case "integration":
    case "group":
    case "job":
      return [{ kind: "project" }, level];
  }
}

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

  const replaceStack = useCallback((next: readonly TopologyLevel[]) => {
    // An empty stack has no `current`, which every consumer reads unguarded.
    setStack(next.length > 0 ? [...next] : ROOT);
  }, []);

  const restore = useCallback((level: TopologyLevel) => {
    setStack(topologyStackForLevel(level));
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
      restore,
      replaceStack,
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
      restore,
      replaceStack,
    ],
  );
}
