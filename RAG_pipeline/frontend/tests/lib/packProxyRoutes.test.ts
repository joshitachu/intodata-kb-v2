import { readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Every pack endpoint the client calls must have a proxy route handler.
 *
 * Written because a former client called `/api/packs/draft` while its route
 * directory was empty, so the Pack author returned
 * 405 with no failing test anywhere -- the client compiles fine against a route
 * that does not exist, and the component tests mock `lib/packs` so they never
 * touch the proxy layer. This is the seam neither side covers.
 *
 * The check is structural (files on disk, and the HTTP verb each exports)
 * rather than a live request, so it runs without a server and fails at the
 * moment a route is added to the client but not to the app directory.
 */

const PACKS_API_DIR = join(process.cwd(), "app", "api", "packs");

/** Endpoints `lib/packs.ts` calls, as (route directory, required verbs). */
const REQUIRED: Array<{ segments: string[]; verbs: string[] }> = [
  { segments: [], verbs: ["GET", "POST"] },
  { segments: ["personal"], verbs: ["POST"] },
  { segments: ["architect", "sessions"], verbs: ["POST"] },
  { segments: ["architect", "sessions", "current"], verbs: ["GET"] },
  { segments: ["architect", "sessions", "[sessionId]"], verbs: ["GET", "DELETE"] },
  { segments: ["architect", "sessions", "[sessionId]", "messages"], verbs: ["POST"] },
  { segments: ["architect", "sessions", "[sessionId]", "messages", "cancel"], verbs: ["POST"] },
  { segments: ["architect", "sessions", "[sessionId]", "save"], verbs: ["POST"] },
  { segments: ["starter"], verbs: ["GET"] },
  { segments: ["prompt-slots"], verbs: ["GET"] },
  { segments: ["prompt-slots", "[...path]"], verbs: ["GET"] },
  { segments: ["discover"], verbs: ["POST"] },
  { segments: ["[packId]"], verbs: ["PATCH", "DELETE"] },
  { segments: ["[packId]", "publish"], verbs: ["POST"] },
  { segments: ["[packId]", "files"], verbs: ["GET"] },
  { segments: ["[packId]", "files", "[...path]"], verbs: ["GET", "PUT"] },
  { segments: ["projects", "[projectId]", "select"], verbs: ["PATCH"] },
];

function routeSource(segments: string[]): string {
  const path = join(PACKS_API_DIR, ...segments, "route.ts");
  // Read via require of the raw file: we assert on exported verb names, and
  // importing the module would pull in next/server at test time.
  return require("node:fs").readFileSync(path, "utf8");
}

describe("pack proxy routes", () => {
  it.each(REQUIRED)(
    "/api/packs/$segments has a route handler",
    ({ segments }) => {
      const dir = join(PACKS_API_DIR, ...segments);
      expect(
        statSync(join(dir, "route.ts")).isFile(),
        `app/api/packs/${segments.join("/")}/route.ts is missing — the client calls it`,
      ).toBe(true);
    },
  );

  it.each(REQUIRED)(
    "/api/packs/$segments exports $verbs",
    ({ segments, verbs }) => {
      const source = routeSource(segments);
      for (const verb of verbs) {
        expect(
          source.includes(`export async function ${verb}(`),
          `app/api/packs/${segments.join("/")}/route.ts must export ${verb}`,
        ).toBe(true);
      }
    },
  );

  it("has no empty route directories", () => {
    const empties: string[] = [];
    const walk = (dir: string, rel: string[]) => {
      const entries = readdirSync(dir, { withFileTypes: true });
      const hasRoute = entries.some((e) => e.isFile() && e.name === "route.ts");
      const subdirs = entries.filter((e) => e.isDirectory());
      if (!hasRoute && subdirs.length === 0) empties.push(rel.join("/") || "packs");
      for (const sub of subdirs) walk(join(dir, sub.name), [...rel, sub.name]);
    };
    walk(PACKS_API_DIR, []);
    // An empty directory under app/api is not a build error in Next.js -- the
    // path simply 405s. That is exactly how the draft route shipped broken.
    expect(empties).toEqual([]);
  });
});
