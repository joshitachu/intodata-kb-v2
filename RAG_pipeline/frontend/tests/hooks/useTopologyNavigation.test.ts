import { describe, it, expect } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useTopologyNavigation } from "@/hooks/useTopologyNavigation";

describe("useTopologyNavigation", () => {
  it("starts at the project level", () => {
    const { result } = renderHook(() => useTopologyNavigation());
    expect(result.current.depth).toBe(1);
    expect(result.current.current).toEqual({ kind: "project" });
    expect(result.current.stack).toEqual([{ kind: "project" }]);
  });

  it("descends into a job level", () => {
    const { result } = renderHook(() => useTopologyNavigation());
    act(() => result.current.descendToJob("jobA"));
    expect(result.current.depth).toBe(2);
    expect(result.current.current).toEqual({ kind: "job", jobName: "jobA" });
  });

  it("keeps the group in the path while exploring one of its designs", () => {
    const { result } = renderHook(() => useTopologyNavigation());
    act(() => result.current.descendToGroup("Root/Project/ITEM"));
    act(() => result.current.descendToJob("OnRamp"));

    expect(result.current.stack).toEqual([
      { kind: "project" },
      { kind: "group", groupName: "Root/Project/ITEM" },
      { kind: "job", jobName: "OnRamp" },
    ]);

    act(() => result.current.ascend());
    expect(result.current.current).toEqual({
      kind: "group",
      groupName: "Root/Project/ITEM",
    });
    act(() => result.current.ascend());
    expect(result.current.current).toEqual({ kind: "project" });
  });

  it("switches groups without retaining a stale deeper path", () => {
    const { result } = renderHook(() => useTopologyNavigation());
    act(() => result.current.descendToGroup("Project/GroupA"));
    act(() => result.current.descendToJob("JobA"));
    act(() => result.current.descendToGroup("Project/GroupB"));

    expect(result.current.stack).toEqual([
      { kind: "project" },
      { kind: "group", groupName: "Project/GroupB" },
    ]);
  });

  it("stacks sibling job hops (tRunJob portal navigation)", () => {
    const { result } = renderHook(() => useTopologyNavigation());
    act(() => result.current.descendToJob("jobA"));
    act(() => result.current.descendToJob("jobB"));
    expect(result.current.depth).toBe(3);
    expect(result.current.stack).toEqual([
      { kind: "project" },
      { kind: "job", jobName: "jobA" },
      { kind: "job", jobName: "jobB" },
    ]);
  });

  it("ignores re-descending into the job already on top", () => {
    const { result } = renderHook(() => useTopologyNavigation());
    act(() => result.current.descendToJob("jobA"));
    act(() => result.current.descendToJob("jobA"));
    expect(result.current.depth).toBe(2);
  });

  it("ascends one level and is a no-op at the root", () => {
    const { result } = renderHook(() => useTopologyNavigation());
    act(() => result.current.descendToJob("jobA"));
    act(() => result.current.ascend());
    expect(result.current.depth).toBe(1);
    act(() => result.current.ascend());
    expect(result.current.depth).toBe(1);
    expect(result.current.current).toEqual({ kind: "project" });
  });

  it("jumps to a breadcrumb index, truncating deeper levels", () => {
    const { result } = renderHook(() => useTopologyNavigation());
    act(() => result.current.descendToJob("jobA"));
    act(() => result.current.descendToJob("jobB"));
    act(() => result.current.jumpTo(1));
    expect(result.current.stack).toEqual([
      { kind: "project" },
      { kind: "job", jobName: "jobA" },
    ]);
  });

  it("ignores jumpTo on the current level or out-of-range indexes", () => {
    const { result } = renderHook(() => useTopologyNavigation());
    act(() => result.current.descendToJob("jobA"));
    const before = result.current.stack;
    act(() => result.current.jumpTo(1)); // current level
    expect(result.current.stack).toBe(before);
    act(() => result.current.jumpTo(5));
    expect(result.current.stack).toBe(before);
    act(() => result.current.jumpTo(-1));
    expect(result.current.stack).toBe(before);
  });

  it("resets to the project level", () => {
    const { result } = renderHook(() => useTopologyNavigation());
    act(() => result.current.descendToJob("jobA"));
    act(() => result.current.descendToJob("jobB"));
    act(() => result.current.reset());
    expect(result.current.depth).toBe(1);
    expect(result.current.current).toEqual({ kind: "project" });
  });

  it("descends from a job level into that job's component graph", () => {
    const { result } = renderHook(() => useTopologyNavigation());
    act(() => result.current.descendToJob("jobA"));
    act(() => result.current.descendToComponent("tMap_1"));
    expect(result.current.depth).toBe(3);
    expect(result.current.current).toEqual({
      kind: "component",
      jobName: "jobA",
      componentName: "tMap_1",
    });
  });

  it("is a no-op descending to a component from the project level", () => {
    const { result } = renderHook(() => useTopologyNavigation());
    act(() => result.current.descendToComponent("tMap_1"));
    expect(result.current.depth).toBe(1);
    expect(result.current.current).toEqual({ kind: "project" });
  });

  it("ignores re-descending into the component already on top", () => {
    const { result } = renderHook(() => useTopologyNavigation());
    act(() => result.current.descendToJob("jobA"));
    act(() => result.current.descendToComponent("tMap_1"));
    act(() => result.current.descendToComponent("tMap_1"));
    expect(result.current.depth).toBe(3);
  });

  it("ascends from a component level back to its job level", () => {
    const { result } = renderHook(() => useTopologyNavigation());
    act(() => result.current.descendToJob("jobA"));
    act(() => result.current.descendToComponent("tMap_1"));
    act(() => result.current.ascend());
    expect(result.current.depth).toBe(2);
    expect(result.current.current).toEqual({ kind: "job", jobName: "jobA" });
  });
});
