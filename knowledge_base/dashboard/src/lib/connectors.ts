import type { ConnectorProvider } from "../types";

/**
 * Provider-neutral vocabulary for live-data sources.
 *
 * Lifted out of `components/connectors/ConnectorsPage.tsx` so the Generate
 * dialog and the Connectors directory describe a provider with the SAME words
 * derived from the SAME field. A second copy is how "Elastic" ended up written
 * into copy that was meant to describe an arbitrary provider.
 *
 * Everything here reads `provider.kind`. Nothing here may branch on
 * `provider_id`: a new connector must inherit correct behaviour from its
 * manifest with no change to any component.
 */

/** The doc-generation engine a run will use. */
export type GenerationEngine = "legacy" | "opencode";

/**
 * Type-column copy. A runtime-sync provider mirrors on a schedule; a
 * live-evidence provider is queried during a run.
 *
 * An unfamiliar kind gets an honest non-answer rather than being described as
 * live evidence, which it may well not be.
 */
export function describeKind(provider: ConnectorProvider): string {
  if (provider.kind === "runtime_sync") return "Runtime sync - mirrored on a schedule";
  if (provider.kind === "live_evidence") {
    return `Live evidence - ${provider.capabilities.length} governed read capabilities`;
  }
  return `Unrecognised source type (${provider.kind})`;
}

/**
 * Which engines actually read a lane, derived from the manifest `kind`.
 *
 * The two lanes are shaped differently for a real reason, and this is the only
 * place that reason is written down:
 *
 *   `live_evidence`  queried IN-BAND during a run, by opencode_cli's
 *                    `_connector_runtime` only. The custom (legacy) harness has
 *                    no connector code path at all, so listing such a source as
 *                    active in a Legacy run promises reads that never happen.
 *   `runtime_sync`   mirrored on a schedule into the RAG backend and read back
 *                    by BOTH engines -- `_ensure_runtime_evidence` is a shared
 *                    preparation step that OpenCode's `_prepare_run` also calls.
 *
 * `null` means this build has not been taught the kind. Say so; do not guess.
 */
export function enginesReadingKind(kind: string): GenerationEngine[] | null {
  if (kind === "runtime_sync") return ["legacy", "opencode"];
  if (kind === "live_evidence") return ["opencode"];
  return null;
}

/** True when the selected engine will actually read a source of this kind. */
export function engineReadsKind(kind: string, engine: GenerationEngine): boolean {
  const readers = enginesReadingKind(kind);
  // An unknown kind is not claimed as inert: the server decides, and a dimmed
  // "will not be read" would be a confident guess about a provider this build
  // does not know.
  return readers === null || readers.includes(engine);
}

/** One sentence naming which engine reads this lane, from the current selection. */
export function describeEngineReach(kind: string, engine: GenerationEngine): string {
  const readers = enginesReadingKind(kind);
  if (readers === null) {
    return "This build cannot say which engine reads this source; the server decides.";
  }
  if (readers.length > 1) return "Read by both the legacy pipeline and OpenCode runs.";
  const only = readers[0];
  const name = only === "opencode" ? "OpenCode runs" : "legacy pipeline runs";
  return only === engine
    ? `Read by ${name}.`
    : `Read by ${name}; this run will not query it.`;
}

/** Absolute timestamp for a sync clock. `null` is never synced, said plainly. */
export function formatSyncedAt(value: string | null): string {
  if (!value) return "never synced";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}
