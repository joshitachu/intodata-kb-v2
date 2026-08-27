import { afterEach, describe, expect, it, vi } from "vitest";
import {
  generateDocsWhenScanned,
  waitForGenerateSettled,
  waitForScanReady,
} from "@/lib/mcpClient";

/**
 * Doc generation must never be dispatched before the scan has produced a usable
 * `scan_result.json`.
 *
 * Written because the pipeline used to trigger generate off a log-text marker
 * ("scan complete"), which can arrive from an earlier run or ahead of the file
 * landing. The result was a real failed run:
 * `FileNotFoundError: No valid fresh scan_result.json found in
 * /app/intodoc-output/nexus`, recorded as a failed generate with docs_staging
 * left behind. The project row is the authority -- it only reaches `ready` after
 * the scanner has written AND parsed that file.
 */

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

/** The slice of Response the client actually touches. */
interface StubResponse {
  ok: boolean;
  status: number;
  json: () => Promise<unknown>;
  text: () => Promise<string>;
}

function stubResponse(body: unknown): StubResponse {
  return {
    ok: true,
    status: 200,
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

/** Serve a sequence of statuses, one per poll; the last one repeats. */
function statuses(...values: Array<Record<string, unknown>>) {
  const queue = [...values];
  const fetchMock = vi.fn(async (url: string): Promise<StubResponse> => {
    if (String(url).includes("/api/source/status/")) {
      return stubResponse(queue.length > 1 ? queue.shift() : queue[0]);
    }
    // /api/source/generate
    return stubResponse({});
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const READY = { project_id: "p", status: "ready" };
const SCANNING = { project_id: "p", status: "scanning" };
const GENERATING = { project_id: "p", status: "generating" };

describe("waitForScanReady", () => {
  it("returns immediately when the scan is already ready", async () => {
    statuses(READY);
    await expect(waitForScanReady("p")).resolves.toMatchObject({ status: "ready" });
  });

  it("keeps polling while the scan is still running", async () => {
    const fetchMock = statuses(SCANNING, SCANNING, READY);

    await expect(waitForScanReady("p", { pollMs: 1 })).resolves.toMatchObject({
      status: "ready",
    });
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("treats a project that has never been scanned as pending, not ready", async () => {
    const fetchMock = statuses({ project_id: "p", status: "registered" }, READY);

    await expect(waitForScanReady("p", { pollMs: 1 })).resolves.toMatchObject({
      status: "ready",
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("surfaces the scan's own error instead of generating against nothing", async () => {
    statuses({ project_id: "p", status: "error", error_message: "scanner blew up" });
    await expect(waitForScanReady("p", { pollMs: 1 })).rejects.toThrow("scanner blew up");
  });

  it("refuses to proceed on an unrecognised status", async () => {
    statuses({ project_id: "p", status: "who-knows" });
    await expect(waitForScanReady("p", { pollMs: 1 })).rejects.toThrow(
      /Unexpected project status 'who-knows'/,
    );
  });

  it("times out rather than polling forever", async () => {
    statuses(SCANNING);
    await expect(waitForScanReady("p", { pollMs: 1, timeoutMs: 5 })).rejects.toThrow(
      /Timed out .* waiting for the scan/,
    );
  });
});

describe("generateDocsWhenScanned", () => {
  it("does not POST generate until the scan reports ready", async () => {
    const fetchMock = statuses(SCANNING, READY);

    await generateDocsWhenScanned("p", { waitForScan: true, pollMs: 1 });

    const calls = fetchMock.mock.calls.map(([url]) => String(url));
    const generateIndex = calls.findIndex((url) => url.includes("/api/source/generate"));
    const statusCalls = calls.slice(0, generateIndex);

    expect(generateIndex).toBeGreaterThan(-1);
    // Every call before the generate was a status poll, and there was more than
    // one -- i.e. it genuinely waited out the "scanning" state.
    expect(statusCalls.length).toBe(2);
    expect(statusCalls.every((url) => url.includes("/api/source/status/"))).toBe(true);
  });

  it("never POSTs generate when the scan failed", async () => {
    const fetchMock = statuses({ project_id: "p", status: "error", error_message: "nope" });

    await expect(generateDocsWhenScanned("p")).rejects.toThrow("nope");
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).includes("/api/source/generate")),
    ).toBe(false);
  });
});

/**
 * The documentation run's completion signal.
 *
 * Scanning and generating are separate user actions, so the UI needs to know when
 * each one has actually finished. It reads the project row for the same reason
 * `waitForScanReady` does: the log markers it used to watch ("generation
 * complete", "pushed to rag") are text, arrive out of order, and can belong to an
 * earlier run.
 */
describe("waitForGenerateSettled", () => {
  it("waits out the generating state, then reports the settled row", async () => {
    const fetchMock = statuses(GENERATING, GENERATING, READY);

    await expect(waitForGenerateSettled("p", { pollMs: 1 })).resolves.toMatchObject({
      status: "ready",
    });
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("surfaces a failed documentation run instead of reporting success", async () => {
    statuses({ project_id: "p", status: "error", error_message: "pack denied" });
    await expect(waitForGenerateSettled("p", { pollMs: 1 })).rejects.toThrow("pack denied");
  });

  it("returns at once when nothing is generating", async () => {
    // POST /mcp/generate commits status='generating' before it answers, so a
    // non-generating row here means the run is already over, not about to start.
    const fetchMock = statuses(READY);
    await expect(waitForGenerateSettled("p")).resolves.toMatchObject({ status: "ready" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("times out rather than polling forever", async () => {
    statuses(GENERATING);
    await expect(
      waitForGenerateSettled("p", { pollMs: 1, timeoutMs: 5 }),
    ).rejects.toThrow(/Timed out .* waiting for the documentation run/);
  });
});
