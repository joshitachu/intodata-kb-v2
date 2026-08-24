/**
 * The pack client. One format, one API, one surface.
 *
 * A pack is a `plugin.yaml` tree that changes what documentation generation
 * looks for and what it writes. It lives in one of two lanes:
 *
 *   personal  authored here, stored against your account, only you can see it
 *   shared    a branch of the pack repository, available to everyone
 *
 * A personal pack becomes shared by being published: we push it as a branch and
 * open a pull request, and a human reviewer decides whether it lands. All of it
 * goes through the MCP server's `/mcp/plugins` endpoints, proxied server-side
 * via `/api/packs/*` so `/mcp/*` is never publicly exposed.
 */

export interface Pack {
  id: number;
  name: string;
  manifest_name: string | null;
  version: string;
  description: string | null;
  repo_url: string | null;
  repo_branch: string | null;
  last_commit_sha: string | null;
  auto_update: boolean;
  origin: string;
  certification_status: string;
  certification_provenance: string | null;
  /** Authored by you. False for shared packs. */
  personal: boolean;
  /** May drive document generation. */
  usable: boolean;
  /** May be edited here (personal packs only). */
  mutable: boolean;
  blocked_reason: string | null;
}

export interface PackBranch {
  branch: string;
  name: string;
  manifest_name: string;
  version: string;
  description: string | null;
  repo_url: string;
  commit_sha: string;
  registered: boolean;
  registered_pack_id: number | null;
}

export interface StarterInfo {
  repo_url: string;
  clone_url: string;
  zip_url: string;
  name: string | null;
  version: string | null;
  description: string | null;
}

export interface PublishResult {
  branch: string;
  repo_url: string;
  commit_sha: string;
  pull_request_url: string | null;
  note: string | null;
}

export interface PackArchitectMessage {
  role: "user" | "assistant";
  content: string;
}

export interface PackArchitectSession {
  id: string;
  status: "active" | "saved" | "abandoned";
  revision: number;
  source_pack_id: number | null;
  platform: string | null;
  model_id: string;
  name: string | null;
  description: string | null;
  messages: PackArchitectMessage[];
  manifest_yaml: string;
  files: Record<string, string>;
  validation_errors: string[];
  complete: boolean;
  turn_status: "idle" | "running" | "failed";
  turn_stage: "queued" | "preparing" | "authoring" | "applying" | "validating" | "complete" | "failed" | null;
  turn_detail: string | null;
  turn_started_at: string | null;
  turn_finished_at: string | null;
  turn_error: string | null;
}

/**
 * A prompt a pack may override.
 *
 * `kind` is load-bearing, not decoration. A `preamble` is substituted INTO a
 * prompt; a `body` IS the prompt and replaces the built-in outright. Mixing
 * them up produces a pack that looks complete and renders a duplicated,
 * unframed prompt, so the editor must label them differently.
 */
export interface PromptSlot {
  path: string;
  phase: string;
  kind: "preamble" | "body" | "document" | "platform_sections";
  title: string;
  description: string;
  default_bytes: number;
  has_default: boolean;
}

export interface PromptSlotContent extends PromptSlot {
  content: string;
}

/** Phase order and labels, mirroring `services/pack_prompt_slots.PHASE_ORDER`. */
export const PHASE_LABELS: Record<string, string> = {
  phase1_investigation: "Phase 1 — investigation",
  phase2_analysis: "Phase 2 — analysis",
  phase26_scorecard: "Phase 2.6 — scorecard",
  phase3_docs: "Phase 3 — documents",
  phase37_business_topology: "Phase 3.7 — business topology",
};

export const KIND_LABELS: Record<PromptSlot["kind"], string> = {
  preamble: "preamble — inserted into the prompt",
  body: "whole prompt — replaces the built-in",
  document: "per-document instruction",
  platform_sections: "platform section guide",
};

export class PackError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "PackError";
    this.status = status;
  }
}

/**
 * Read the error the server actually sent.
 *
 * FastAPI puts a string in `detail` for raised HTTPExceptions and an ARRAY of
 * per-field objects for 422 validation errors. Treating only the string case as
 * real is why "Pack registry request failed" used to be the only thing anyone
 * ever saw; the array case is exactly where the useful message lives.
 */
async function packError(response: Response, fallback: string): Promise<PackError> {
  const text = await response.text();
  try {
    const body = JSON.parse(text);
    const detail = body?.detail ?? body?.error;
    if (typeof detail === "string" && detail) {
      return new PackError(detail, response.status);
    }
    if (Array.isArray(detail)) {
      const parts = detail
        .map((item) => {
          if (!item || typeof item !== "object") return String(item);
          const field = Array.isArray(item.loc)
            ? item.loc.filter((p: unknown) => p !== "body").join(".")
            : "";
          return field ? `${field}: ${item.msg}` : item.msg;
        })
        .filter(Boolean);
      if (parts.length) return new PackError(parts.join("; "), response.status);
    }
  } catch {
    // Not JSON: an upstream 500 renders as plain text. Show it rather than
    // swallowing it -- a bare "request failed" is a dead end for the user.
    if (text.trim()) {
      return new PackError(text.trim().slice(0, 300), response.status);
    }
  }
  return new PackError(`${fallback} (HTTP ${response.status})`, response.status);
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  fallback = "Pack request failed",
): Promise<T> {
  const response = await fetch(`/api/packs${path}`, {
    credentials: "include",
    ...init,
    headers: init.body ? { "Content-Type": "application/json", ...init.headers } : init.headers,
  });
  if (!response.ok) throw await packError(response, fallback);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** Every pack you can use: shared ones, plus your own. */
export function listPacks(): Promise<Pack[]> {
  return request<Pack[]>("", {}, "Could not load packs");
}

export function createPersonalPack(input: {
  name: string;
  description?: string;
  manifestYaml?: string;
  files?: Record<string, string>;
  inheritStarter?: boolean;
  platform?: string;
}): Promise<Pack> {
  return request<Pack>(
    "/personal",
    {
      method: "POST",
      body: JSON.stringify({
        name: input.name,
        description: input.description,
        manifest_yaml: input.manifestYaml,
        files: input.files,
        inherit_starter: input.inheritStarter,
        platform: input.platform,
      }),
    },
    "Could not create the pack",
  );
}

export function deletePack(packId: number): Promise<void> {
  return request<void>(`/${packId}`, { method: "DELETE" }, "Could not delete the pack");
}

export function listPackFiles(packId: number): Promise<string[]> {
  return request<string[]>(`/${packId}/files`, {}, "Could not list the pack files");
}

export function readPackFile(
  packId: number,
  filePath: string,
): Promise<{ path: string; content: string }> {
  return request(
    `/${packId}/files/${filePath.split("/").map(encodeURIComponent).join("/")}`,
    {},
    "Could not read the file",
  );
}

export function writePackFile(
  packId: number,
  filePath: string,
  content: string,
): Promise<{ path: string; content: string }> {
  return request(
    `/${packId}/files/${filePath.split("/").map(encodeURIComponent).join("/")}`,
    { method: "PUT", body: JSON.stringify({ content }) },
    "Could not save the file",
  );
}

/** Push the pack as a branch and open its pull request. Asks; does not grant. */
export function publishPack(
  packId: number,
  input: { branch?: string; title?: string; body?: string } = {},
): Promise<PublishResult> {
  return request<PublishResult>(
    `/${packId}/publish`,
    { method: "POST", body: JSON.stringify(input) },
    "Could not publish the pack",
  );
}

export function starterInfo(): Promise<StarterInfo> {
  return request<StarterInfo>("/starter", {}, "Could not load the starter");
}

/** Branches of the shared repository that are valid packs. */
export function discoverSharedBranches(repoUrl?: string): Promise<{
  repo_url: string;
  branches: PackBranch[];
}> {
  return request(
    "/discover",
    { method: "POST", body: JSON.stringify({ repo_url: repoUrl }) },
    "Could not load the shared pack branches",
  );
}

export function registerSharedBranch(repoUrl: string, branch: string): Promise<Pack> {
  return request<Pack>(
    "",
    { method: "POST", body: JSON.stringify({ repo_url: repoUrl, repo_branch: branch }) },
    "Could not register the branch",
  );
}

export function pullSharedPack(packId: number): Promise<Pack> {
  return request<Pack>(
    `/${packId}`,
    { method: "PATCH", body: JSON.stringify({ pull: true }) },
    "Could not update the pack",
  );
}

/** Set (or clear, with null) the pack a project generates with. */
export function selectProjectPack(projectId: string, packId: number | null): Promise<unknown> {
  return request(
    `/projects/${encodeURIComponent(projectId)}/select`,
    { method: "PATCH", body: JSON.stringify({ plugin_pack_id: packId }) },
    "Could not select the pack",
  );
}

export function createPackArchitectSession(
  platform?: string,
  packId?: number,
): Promise<PackArchitectSession> {
  return request(
    "/architect/sessions",
    {
      method: "POST",
      body: JSON.stringify({
        platform: platform || undefined,
        pack_id: packId,
      }),
    },
    "Could not start the Pack Architect",
  );
}

export function getPackArchitectSession(sessionId: string): Promise<PackArchitectSession> {
  return request(
    `/architect/sessions/${encodeURIComponent(sessionId)}`,
    {},
    "Could not load the Pack Architect session",
  );
}

export function getCurrentPackArchitectSession(): Promise<PackArchitectSession> {
  return request(
    "/architect/sessions/current",
    {},
    "Could not recover the active Pack Architect session",
  );
}

export function sendPackArchitectMessage(
  sessionId: string,
  message: string,
  expectedRevision: number,
): Promise<PackArchitectSession> {
  return request(
    `/architect/sessions/${encodeURIComponent(sessionId)}/messages`,
    {
      method: "POST",
      body: JSON.stringify({ message, expected_revision: expectedRevision }),
    },
    "The Pack Architect could not complete that turn",
  );
}

export function cancelPackArchitectTurn(
  sessionId: string,
  expectedRevision: number,
): Promise<PackArchitectSession> {
  return request(
    `/architect/sessions/${encodeURIComponent(sessionId)}/messages/cancel`,
    {
      method: "POST",
      body: JSON.stringify({ expected_revision: expectedRevision }),
    },
    "Could not cancel the Pack Architect turn",
  );
}

export function abandonPackArchitectSession(
  sessionId: string,
  expectedRevision: number,
): Promise<void> {
  return request(
    `/architect/sessions/${encodeURIComponent(sessionId)}`,
    {
      method: "DELETE",
      body: JSON.stringify({ expected_revision: expectedRevision }),
    },
    "Could not abandon the Pack draft",
  );
}

export function savePackArchitectSession(
  sessionId: string,
  name: string,
  expectedRevision: number,
): Promise<Pack> {
  return request(
    `/architect/sessions/${encodeURIComponent(sessionId)}/save`,
    {
      method: "POST",
      body: JSON.stringify({ name, expected_revision: expectedRevision }),
    },
    "Could not save the Pack",
  );
}

/** Every prompt the pipeline reads, in phase order. */
export function listPromptSlots(): Promise<PromptSlot[]> {
  return request<PromptSlot[]>("/prompt-slots", {}, "Could not load the prompt list");
}

/**
 * The built-in text for one slot.
 *
 * The point of this call is that an override REPLACES the built-in, so starting
 * from an empty box silently drops whatever the default said.
 */
export function readPromptSlot(path: string): Promise<PromptSlotContent> {
  return request<PromptSlotContent>(
    `/prompt-slots/${path.split("/").map(encodeURIComponent).join("/")}`,
    {},
    "Could not load the built-in prompt",
  );
}

/** Group slots by phase, dropping phases with no prompts. */
export function groupSlotsByPhase(slots: PromptSlot[]): Array<{
  phase: string;
  label: string;
  slots: PromptSlot[];
}> {
  const order = Object.keys(PHASE_LABELS);
  const seen = new Map<string, PromptSlot[]>();
  for (const slot of slots) {
    const list = seen.get(slot.phase);
    if (list) list.push(slot);
    else seen.set(slot.phase, [slot]);
  }
  // Sort by the known phase order, then anything unrecognised (a phase added
  // server-side before this map was updated) at the end rather than dropped.
  const phases = [...seen.keys()].sort((a, b) => {
    const ai = order.indexOf(a);
    const bi = order.indexOf(b);
    return (ai === -1 ? order.length : ai) - (bi === -1 ? order.length : bi);
  });
  return phases.map((phase) => ({
    phase,
    label: PHASE_LABELS[phase] ?? phase,
    slots: seen.get(phase) ?? [],
  }));
}

/** Shared packs first, then your own -- matching the server's ordering. */
export function groupPacks(packs: Pack[]): { shared: Pack[]; mine: Pack[] } {
  return {
    shared: packs.filter((p) => !p.personal),
    mine: packs.filter((p) => p.personal),
  };
}

export function packSubtitle(pack: Pack): string {
  if (pack.personal) return `Yours · v${pack.version}`;
  const sha = pack.last_commit_sha ? ` · ${pack.last_commit_sha.slice(0, 7)}` : "";
  return `${pack.repo_branch ?? "shared"} · v${pack.version}${sha}`;
}

// --------------------------------------------------------------------------
// URL contract for the workbench panel
// --------------------------------------------------------------------------
// `?workspace=pack` opens the workbench beside the chat. Kept as a URL rather
// than component state so the harness can link a user straight to it and so the
// panel survives a reload.

export const PACK_WORKSPACE_PARAM = "workspace";
export const PACK_WORKSPACE_VALUE = "pack";

export function packWorkbenchHref(
  options: { conversationId?: string | number | null; projectId?: string | null } = {},
): string {
  const params = new URLSearchParams();
  if (options.conversationId) params.set("c", String(options.conversationId));
  if (options.projectId) params.set("project", options.projectId);
  params.set(PACK_WORKSPACE_PARAM, PACK_WORKSPACE_VALUE);
  return `/?${params.toString()}`;
}

/** The same URL with the panel closed, preserving everything else. */
export function closePackWorkbenchHref(searchParams: URLSearchParams): string {
  const next = new URLSearchParams(searchParams.toString());
  next.delete(PACK_WORKSPACE_PARAM);
  next.delete("pack");
  next.delete("tab");
  const query = next.toString();
  return query ? `/?${query}` : "/";
}
