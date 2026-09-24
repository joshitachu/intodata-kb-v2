"use client";

import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { AlertTriangle, Link2, Trash2 } from "lucide-react";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { SelectMenu } from "@/components/ui/select-menu";
import {
  RuntimeSourceReadGrantListSchema,
  RuntimeSourceReadGrantResponseSchema,
  type RuntimeSourceReadGrant,
} from "@/types/runtime";
import { apiErrorMessage } from "@/lib/apiError";
import { cn } from "@/lib/utils";

/**
 * Which projects are attached to a registered runtime source.
 *
 * A runtime source has NO owning project. It holds one vendor tenant (a TMC or
 * Elasticsearch instance), which can be registered exactly once --
 * `runtime_sources.physical_identity` is globally unique -- because a second
 * registration would poll the vendor twice and leave two mirrors of one estate
 * free to drift apart. Projects ATTACH to that single mirror, at registration
 * or at any time afterwards, and detach the same way.
 *
 * A source with no attachments is a normal state, not a broken one: the tenant
 * is registered and mirrored, and nothing reads it yet.
 *
 * Deliberately NOT merged with the container-mapping control next to it. A
 * container map SPLITS one source's rows between projects at write time; an
 * attachment gives a project read access to the SAME rows. One combined
 * control is how an operator picks the wrong one.
 */

export interface ProjectOption {
  id: string;
  display_name: string;
}

/**
 * One short line that answers the question operators actually hesitate on.
 *
 * Operators assume attaching a second project means paying the vendor twice.
 * It does not, and saying so is what stops a needless second registration.
 */
const ONE_MIRROR_NOTE =
  "The tenant is mirrored once and polled once, however many projects attach to it. Attaching a project shares the mirror that already exists, so it adds no extra load at the vendor.";

async function readError(res: Response, fallback: string): Promise<string> {
  const body = await res.json().catch(() => null);
  const named = (body as { error?: unknown } | null)?.error;
  if (typeof named === "string" && named.trim()) return named;
  return apiErrorMessage((body as { detail?: unknown } | null)?.detail, fallback);
}

/**
 * Read the projects attached to a source.
 *
 * Exported so the row summary and the dialog ask the same question of the same
 * endpoint; two call sites drifting apart is how a row came to claim a source
 * had no projects while its dialog listed two.
 */
export async function fetchAttachedProjects(
  sourceId: number,
): Promise<RuntimeSourceReadGrant[]> {
  const res = await fetch(`/api/runtime/sources/${sourceId}/readers`, { cache: "no-store" });
  if (!res.ok) throw new Error(await readError(res, "Could not load the attached projects"));
  return RuntimeSourceReadGrantListSchema.parse(await res.json()).grants;
}

/** Mirrored environments offered for the optional narrowing. */
const MirroredEnvironmentSchema = z.object({
  environment_id: z.string(),
  name: z.string(),
  run_count: z.number().optional().default(0),
});
const DocReadScopeResponseSchema = z.object({
  available_environments: z.array(MirroredEnvironmentSchema).default([]),
});
type MirroredEnvironment = z.infer<typeof MirroredEnvironmentSchema>;

export function projectLabel(projectId: string, projects: ProjectOption[]): string {
  return projects.find((project) => project.id === projectId)?.display_name ?? projectId;
}

/**
 * The projects an attachment exposes BEYOND the source's own estate.
 *
 * `exposes_project_ids` is the attachment's real reach: the source's estate key
 * plus every project its container map re-points rows to. More than one entry
 * means the attachment hands over a third party's data, which the operator has
 * to be told -- the backend computes this field for no other reason.
 */
export function extraExposedProjects(grant: RuntimeSourceReadGrant): string[] {
  if (grant.exposes_project_ids.length <= 1) return [];
  const others = grant.exposes_project_ids.filter((id) => id !== grant.source_estate_key);
  return others.length > 0 ? others : grant.exposes_project_ids;
}

/**
 * Projects that may still be attached.
 *
 * Derived from live API responses, never a fixed list: an already-attached
 * project is answered 409 by the backend, so offering it would be an option
 * that cannot succeed. There is no owning project to exclude any more -- the
 * project named at registration is simply the first attachment.
 */
export function eligibleProjects(
  projects: ProjectOption[],
  grants: RuntimeSourceReadGrant[],
): ProjectOption[] {
  const attached = new Set(grants.map((grant) => grant.project_id));
  return projects.filter((project) => !attached.has(project.id));
}

/**
 * What the sources table shows for a source before anything is clicked.
 *
 * The state has to be legible at rest: the previous surface for this feature
 * was a collapsed grey text button in another application, and it was so
 * invisible that the feature was believed not to exist.
 *
 * Zero attachments is styled exactly like any other count -- neutral, never a
 * warning. A tenant registered ahead of its projects is a normal intermediate
 * state, and a red or amber cell would send an operator hunting for a fault
 * that does not exist.
 */
export function AttachedProjectsCell({
  grants,
  loading,
  error,
  canManage,
  onOpen,
}: {
  grants: RuntimeSourceReadGrant[] | undefined;
  loading: boolean;
  error: string | null;
  canManage: boolean;
  onOpen: () => void;
}) {
  if (error) {
    return (
      <span className="text-xs text-muted-foreground" title={error}>
        Attachments unavailable
      </span>
    );
  }
  if (loading || grants === undefined) {
    return <span className="text-xs text-muted-foreground">Checking...</span>;
  }

  const count = grants.length;
  const names = grants.map((grant) => grant.project_id).join(", ");

  // Nothing to open and nothing to change: a button here would promise an
  // action this user cannot take.
  if (count === 0 && !canManage) {
    return <span className="text-xs text-muted-foreground">No projects attached</span>;
  }

  return (
    <button
      type="button"
      onClick={onOpen}
      title={count > 0 ? `Attached to ${names}` : ONE_MIRROR_NOTE}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs font-medium transition-colors",
        count > 0
          ? "border-primary/30 bg-primary/10 text-primary hover:bg-primary/15"
          : "border-border text-muted-foreground hover:bg-muted hover:text-foreground",
      )}
    >
      <Link2 className="h-3 w-3 shrink-0" aria-hidden />
      {count === 0
        ? "Attach a project"
        : `${count} ${count === 1 ? "project" : "projects"} attached`}
    </button>
  );
}

/** Human rendering of an attachment's environment narrowing. */
function environmentSummary(grant: RuntimeSourceReadGrant): {
  text: string;
  warn: boolean;
} {
  // null and [] are different answers and must not be collapsed: null is every
  // mirrored environment, [] is a deliberate "nothing" that reads no data.
  if (grant.environment_ids === null) return { text: "All environments", warn: false };
  if (grant.environment_ids.length === 0) {
    return { text: "No environments - this project can read nothing", warn: true };
  }
  return { text: `Environments: ${grant.environment_ids.join(", ")}`, warn: false };
}

export function SourceAttachmentsDialog({
  source,
  projects,
  canManage,
  onClose,
  onChanged,
}: {
  source: { id: number; label: string } | null;
  projects: ProjectOption[];
  canManage: boolean;
  onClose: () => void;
  onChanged: () => void;
}) {
  const sourceId = source?.id ?? null;

  const [grants, setGrants] = useState<RuntimeSourceReadGrant[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [projectId, setProjectId] = useState("");
  const [narrowing, setNarrowing] = useState(false);
  const [environments, setEnvironments] = useState<MirroredEnvironment[] | null>(null);
  const [environmentError, setEnvironmentError] = useState<string | null>(null);
  const [selectedEnvironments, setSelectedEnvironments] = useState<string[]>([]);
  const [windowDays, setWindowDays] = useState("");

  const [removeTarget, setRemoveTarget] = useState<RuntimeSourceReadGrant | null>(null);
  const [exposureNotice, setExposureNotice] = useState<RuntimeSourceReadGrant | null>(null);

  const load = useCallback(async () => {
    if (sourceId === null) return;
    setLoadError(null);
    try {
      setGrants(await fetchAttachedProjects(sourceId));
    } catch (err) {
      setGrants(null);
      setLoadError(err instanceof Error ? err.message : "Could not load the attached projects");
    }
  }, [sourceId]);

  useEffect(() => {
    if (sourceId === null) {
      setGrants(null);
      setLoadError(null);
      setActionError(null);
      setNarrowing(false);
      setEnvironments(null);
      setEnvironmentError(null);
      setSelectedEnvironments([]);
      setWindowDays("");
      setRemoveTarget(null);
      setExposureNotice(null);
      return;
    }
    void load();
  }, [sourceId, load]);

  const eligible = useMemo(() => eligibleProjects(projects, grants ?? []), [projects, grants]);

  useEffect(() => {
    setProjectId((current) =>
      eligible.some((project) => project.id === current) ? current : eligible[0]?.id ?? "",
    );
  }, [eligible]);

  /**
   * The project the mirror is read through when listing environments.
   *
   * `/runtime/doc-read-scope` answers for a PROJECT the caller can see, so the
   * probe has to be an attached project -- the source's estate key is a row
   * stamp, not a project, and asking for it would 404. With nothing attached
   * yet there is no such project, and the narrowing says so instead of
   * pretending the mirror is empty.
   */
  const scopeProjectId = grants?.[0]?.project_id ?? null;

  /**
   * Environment options come from the MIRROR, not from a live provider call:
   * an environment that was never ingested would narrow the attachment down to
   * no data at all, with no visible cause. Fetched only when the operator opens
   * the optional section, because the common case never needs it.
   */
  useEffect(() => {
    if (!narrowing || !scopeProjectId || environments !== null) return;
    let cancelled = false;
    void (async () => {
      try {
        const res = await fetch(
          `/api/runtime/doc-read-scope?project_id=${encodeURIComponent(scopeProjectId)}`,
          { cache: "no-store" },
        );
        if (cancelled) return;
        if (!res.ok) {
          setEnvironmentError(await readError(res, "Could not load the mirrored environments"));
          return;
        }
        setEnvironments(DocReadScopeResponseSchema.parse(await res.json()).available_environments);
      } catch {
        if (!cancelled) setEnvironmentError("Could not load the mirrored environments");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [narrowing, scopeProjectId, environments]);

  function toggleEnvironment(id: string) {
    setSelectedEnvironments((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id],
    );
  }

  const parsedWindow = Number.parseInt(windowDays, 10);
  const windowInvalid =
    windowDays.trim() !== "" && (!Number.isFinite(parsedWindow) || parsedWindow < 1 || parsedWindow > 365);
  // A narrowing with nothing selected is the deliberate "nothing" state: it
  // would be accepted and would read no data at all. Blocked here rather than
  // silently rewritten to "everything", which would give away more than asked.
  const emptyNarrowing = narrowing && selectedEnvironments.length === 0;

  async function attachProject(event: FormEvent) {
    event.preventDefault();
    if (sourceId === null || !projectId || emptyNarrowing || windowInvalid) return;
    setBusy(true);
    setActionError(null);
    try {
      const res = await fetch(`/api/runtime/sources/${sourceId}/readers`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          project_id: projectId,
          // null is "every mirrored environment" and is the default path.
          environment_ids: narrowing ? selectedEnvironments : null,
          window_days: windowDays.trim() === "" ? null : parsedWindow,
        }),
      });
      if (!res.ok) {
        setActionError(await readError(res, "Could not attach this project"));
        return;
      }
      const created = RuntimeSourceReadGrantResponseSchema.safeParse(await res.json());
      // Non-blocking: the attachment already happened. The operator is told what
      // it actually reached, rather than being asked to approve it after the
      // fact.
      if (created.success && extraExposedProjects(created.data.grant).length > 0) {
        setExposureNotice(created.data.grant);
      }
      // The narrowing controls are left exactly as the operator set them. They
      // are on screen, so repeating them is a visible choice; silently clearing
      // them would widen the next attachment without saying so.
      await load();
      onChanged();
    } catch {
      setActionError("Network error while attaching this project");
    } finally {
      setBusy(false);
    }
  }

  async function detachProject(grant: RuntimeSourceReadGrant) {
    if (sourceId === null) return;
    setBusy(true);
    setActionError(null);
    try {
      const res = await fetch(
        `/api/runtime/sources/${sourceId}/readers/${encodeURIComponent(grant.project_id)}`,
        { method: "DELETE" },
      );
      if (!res.ok && res.status !== 204) {
        setActionError(await readError(res, "Could not detach this project"));
        return;
      }
      setRemoveTarget(null);
      await load();
      onChanged();
    } catch {
      setActionError("Network error while detaching the project");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={source !== null}
      title={`Attached projects - ${source?.label ?? ""}`}
      description="Projects attached here can read this source's imported runtime data. They do not get its credentials."
      onClose={onClose}
      size="lg"
      footer={
        <Button variant="secondary" size="sm" onClick={onClose}>
          Close
        </Button>
      }
    >
      <div className="space-y-4">
        <p className="rounded-md border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
          {ONE_MIRROR_NOTE}
        </p>

        <section className="space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Projects using this source
          </h3>

          {loadError ? (
            <p
              role="alert"
              className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive"
            >
              {loadError}
            </p>
          ) : grants === null ? (
            <p className="text-sm text-muted-foreground">Loading the attached projects...</p>
          ) : grants.length === 0 ? (
            // Deliberately neutral, not an alert: the tenant is registered and
            // mirrored, it simply has no readers yet.
            <p className="rounded-md border border-dashed border-border px-3 py-4 text-center text-sm text-muted-foreground">
              No projects are attached yet. This source is registered and mirrored; attach a
              project below so its documentation runs can read it.
            </p>
          ) : (
            <ul className="space-y-2">
              {grants.map((grant) => {
                const environment = environmentSummary(grant);
                const alsoExposes = extraExposedProjects(grant);
                const confirming = removeTarget?.project_id === grant.project_id;
                return (
                  <li
                    key={grant.id}
                    className="rounded-lg border border-border px-3 py-2 text-sm"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <span className="min-w-0">
                        <span className="block truncate font-medium text-foreground">
                          {projectLabel(grant.project_id, projects)}
                        </span>
                        <span
                          className={cn(
                            "block truncate text-xs",
                            environment.warn ? "text-amber-700 dark:text-amber-400" : "text-muted-foreground",
                          )}
                        >
                          {environment.text}
                          {grant.window_days !== null && ` - last ${grant.window_days} days`}
                        </span>
                      </span>
                      {canManage && !confirming && (
                        <button
                          type="button"
                          aria-label={`Detach ${projectLabel(grant.project_id, projects)}`}
                          title="Detach this project"
                          onClick={() => {
                            setActionError(null);
                            setRemoveTarget(grant);
                          }}
                          className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:border-destructive/40 hover:bg-destructive/10 hover:text-destructive"
                        >
                          <Trash2 className="h-3.5 w-3.5" aria-hidden />
                        </button>
                      )}
                    </div>

                    {alsoExposes.length > 0 && (
                      <p className="mt-1.5 text-xs text-amber-700 dark:text-amber-400">
                        Also exposes data belonging to{" "}
                        {alsoExposes.map((id) => projectLabel(id, projects)).join(", ")}.
                      </p>
                    )}

                    {confirming && (
                      <div
                        role="alertdialog"
                        aria-label="Confirm detaching this project"
                        className="mt-2 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2"
                      >
                        <p className="text-xs text-destructive">
                          Detach {projectLabel(grant.project_id, projects)}? Their documentation
                          runs will lose this runtime history at the next run.
                        </p>
                        <div className="mt-2 flex gap-2">
                          <Button
                            variant="destructive"
                            size="sm"
                            isLoading={busy}
                            onClick={() => void detachProject(grant)}
                          >
                            Detach
                          </Button>
                          <Button
                            variant="secondary"
                            size="sm"
                            onClick={() => setRemoveTarget(null)}
                          >
                            Keep attached
                          </Button>
                        </div>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        {exposureNotice && (
          <div
            role="status"
            className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-xs text-amber-800 dark:text-amber-400"
          >
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
            <span className="min-w-0 flex-1">
              {projectLabel(exposureNotice.project_id, projects)} can now also read runtime data
              belonging to{" "}
              {extraExposedProjects(exposureNotice)
                .map((id) => projectLabel(id, projects))
                .join(", ")}
              , because this source sends some of its rows to those projects.
            </span>
            <button
              type="button"
              onClick={() => setExposureNotice(null)}
              className="shrink-0 underline opacity-80 hover:opacity-100"
            >
              dismiss
            </button>
          </div>
        )}

        {canManage ? (
          <form className="space-y-3 border-t border-border/60 pt-4" onSubmit={attachProject}>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Attach a project
            </h3>

            {grants === null && !loadError ? (
              <p className="text-sm text-muted-foreground">Loading projects...</p>
            ) : eligible.length === 0 ? (
              <p
                role="status"
                className="rounded-md border border-border bg-muted/40 px-3 py-2 text-sm text-muted-foreground"
              >
                Every project you can see is already attached to this source.
              </p>
            ) : (
              <>
                <SelectMenu
                  value={projectId}
                  onChange={setProjectId}
                  options={eligible.map((project) => ({
                    value: project.id,
                    label: project.display_name,
                  }))}
                  placeholder="Select a project"
                  ariaLabel="Project to attach"
                  variant="field"
                  size="md"
                  className="w-full max-w-none"
                />

                <details
                  className="rounded-md border border-border bg-muted/20 px-3 py-2"
                  onToggle={(event) => setNarrowing(event.currentTarget.open)}
                >
                  <summary className="cursor-pointer text-xs font-medium text-muted-foreground">
                    Limit what they can read (optional)
                  </summary>
                  <div className="mt-2 space-y-3">
                    <p className="text-xs text-muted-foreground">
                      Leave this closed to share everything in the mirror. That is the usual
                      choice.
                    </p>

                    {environmentError ? (
                      <p role="alert" className="text-xs text-destructive">
                        {environmentError}
                      </p>
                    ) : scopeProjectId === null ? (
                      <p className="text-xs text-muted-foreground">
                        No project is attached yet, so the mirror cannot be read to list its
                        environments. Attach the first project to everything, then narrow any
                        later one.
                      </p>
                    ) : environments === null ? (
                      <p className="text-xs text-muted-foreground">Reading the mirror...</p>
                    ) : environments.length === 0 ? (
                      <p className="text-xs text-muted-foreground">
                        Nothing has been imported yet, so there are no environments to choose
                        from.
                      </p>
                    ) : (
                      <ul className="space-y-1">
                        {environments.map((environment) => (
                          <li key={environment.environment_id}>
                            <label className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm hover:bg-muted">
                              <input
                                type="checkbox"
                                className="h-4 w-4 accent-primary"
                                checked={selectedEnvironments.includes(environment.environment_id)}
                                onChange={() => toggleEnvironment(environment.environment_id)}
                              />
                              <span className="font-medium">{environment.name}</span>
                              <span className="text-xs text-muted-foreground">
                                {environment.run_count} runs
                              </span>
                            </label>
                          </li>
                        ))}
                      </ul>
                    )}

                    {emptyNarrowing && (
                      <p role="alert" className="text-xs text-amber-700 dark:text-amber-400">
                        Pick at least one environment, or close this section to share every
                        environment. An empty selection would share nothing.
                      </p>
                    )}

                    <label className="block space-y-1 text-xs font-medium text-muted-foreground">
                      History window in days (optional - blank shares all history)
                      <Input
                        type="number"
                        min={1}
                        max={365}
                        value={windowDays}
                        onChange={(event) => setWindowDays(event.target.value)}
                        placeholder="All history"
                      />
                    </label>
                    {windowInvalid && (
                      <p role="alert" className="text-xs text-destructive">
                        The history window must be between 1 and 365 days.
                      </p>
                    )}
                  </div>
                </details>

                <Button
                  type="submit"
                  size="md"
                  isLoading={busy}
                  disabled={!projectId || emptyNarrowing || windowInvalid}
                >
                  <Link2 className="h-3.5 w-3.5" aria-hidden />
                  Attach this project
                </Button>
              </>
            )}

            {actionError && (
              <p
                role="alert"
                className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive"
              >
                {actionError}
              </p>
            )}
          </form>
        ) : (
          <p
            role="status"
            className="border-t border-border/60 pt-4 text-sm text-muted-foreground"
          >
            You can see which projects are attached, but changing that needs runtime source write
            access.
          </p>
        )}
      </div>
    </Modal>
  );
}
