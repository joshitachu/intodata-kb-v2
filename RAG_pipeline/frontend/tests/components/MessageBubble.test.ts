import { describe, expect, it } from "vitest";
import { currentActivityLabel } from "@/components/MessageBubble";
import type {
  AgentStep,
  HarnessActivityEvent,
  RuntimeToolProgress,
} from "@/types";

function step(overrides: Partial<AgentStep> = {}): AgentStep {
  return { id: "s1", label: "Searching documents", status: "running", ...overrides };
}

function activity(
  overrides: Partial<HarnessActivityEvent> = {},
): HarnessActivityEvent {
  return {
    event_type: "capability.started",
    label: "Starting documentation generation",
    status: "running",
    summary: null,
    safe_details: {},
    ...overrides,
  };
}

function tool(overrides: Partial<RuntimeToolProgress> = {}): RuntimeToolProgress {
  return {
    toolName: "detect_failures",
    status: "running",
    latencyMs: null,
    truncated: false,
    error: null,
    ...overrides,
  };
}

describe("currentActivityLabel", () => {
  it("prefers a running agent step label above all other signals", () => {
    const label = currentActivityLabel({
      latestRunningStep: step({ label: "Searching knowledge base" }),
      harnessActivities: [activity()],
      runtimeTools: [tool()],
      hasSources: true,
    });

    expect(label).toBe("Searching knowledge base");
  });

  it("ignores completed agent steps and falls through to other signals", () => {
    const label = currentActivityLabel({
      latestRunningStep: step({ label: "Completed step", status: "completed" }),
      harnessActivities: [],
      runtimeTools: [],
      hasSources: false,
    });

    expect(label).toBe("Searching...");
  });

  it("surfaces the latest running harness activity when no step is running", () => {
    const label = currentActivityLabel({
      harnessActivities: [
        activity({ label: "Finished step", status: "completed" }),
        activity({ label: "Scanning integration code", status: "running" }),
      ],
    });

    expect(label).toBe("Scanning integration code");
  });

  it("ignores completed harness activities and falls through", () => {
    const label = currentActivityLabel({
      harnessActivities: [activity({ status: "completed" })],
    });

    expect(label).toBe("Searching...");
  });

  it("surfaces a running runtime tool when no step or harness activity is running", () => {
    const label = currentActivityLabel({
      runtimeTools: [tool({ toolName: "get_execution_logs", status: "running" })],
    });

    expect(label).toBe("Get execution logs...");
  });

  it("shows Thinking... when sources have arrived but nothing is running", () => {
    const label = currentActivityLabel({ hasSources: true });

    expect(label).toBe("Thinking...");
  });

  it("falls back to Searching... before sources arrive", () => {
    expect(currentActivityLabel({})).toBe("Searching...");
  });
});
