"use client";

import { useEffect, useState } from "react";
import { AlertCircle, AlertTriangle, FileText, Info, Loader2, RotateCcw } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { listPacks, packSubtitle, selectProjectPack, type Pack } from "@/lib/packs";
import { getGenerationPlan, type GenerationPlan } from "@/lib/mcpClient";

/**
 * Pack choice for one documentation run.
 *
 * Generation used to fire automatically the moment a scan finished, which meant
 * the first run always used whatever pack the project happened to have -- the
 * user was never asked. Generating is now an explicit action, so this is where
 * the question gets asked.
 *
 * `null` means "no pack": generation falls back to standard documentation. That
 * is a real, selectable answer, not an absence of one, so it gets its own row
 * rather than being the state of having picked nothing.
 */
export interface GenerateChoice {
  packId: number | null;
  /** Save the choice as the project's default as well as using it for this run. */
  remember: boolean;
}

interface Props {
  open: boolean;
  /** Project the run is for. Used to ask the server what this run would cost. */
  projectId: string;
  projectLabel: string;
  /** The project's currently saved pack, preselected. */
  currentPackId: number | null;
  onCancel: () => void;
  onConfirm: (choice: GenerateChoice) => void | Promise<void>;
}

export function GeneratePackDialog({
  open,
  projectId,
  projectLabel,
  currentPackId,
  onCancel,
  onConfirm,
}: Props) {
  const [packs, setPacks] = useState<Pack[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState<number | null>(currentPackId);
  const [remember, setRemember] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [plan, setPlan] = useState<GenerationPlan | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  const [planLoading, setPlanLoading] = useState(false);

  // Reset on each open so a cancelled dialog does not reopen holding the
  // abandoned selection.
  useEffect(() => {
    if (!open) return;
    setSelected(currentPackId);
    setRemember(false);
    setLoadError(null);
    setPacks(null);
    let cancelled = false;
    listPacks()
      .then((result) => {
        if (!cancelled) setPacks(result);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setLoadError(err instanceof Error ? err.message : String(err));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [currentPackId, open]);

  // The plan is a separate request from the pack list on purpose: it is advisory
  // and must never gate the run. If it fails, the dialog is still the dialog it
  // was before this preview existed, with a line saying the estimate is missing.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setPlan(null);
    setPlanError(null);
    setPlanLoading(true);
    getGenerationPlan(projectId)
      .then((result) => {
        if (!cancelled) setPlan(result);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setPlanError(err instanceof Error ? err.message : String(err));
        }
      })
      .finally(() => {
        if (!cancelled) setPlanLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, projectId]);

  async function confirm() {
    setSubmitting(true);
    try {
      await onConfirm({ packId: selected, remember });
    } finally {
      setSubmitting(false);
    }
  }

  const usable = (packs ?? []).filter((pack) => pack.usable);
  const blocked = (packs ?? []).filter((pack) => !pack.usable);
  // A pack the project points at but which no longer generates would otherwise
  // vanish from the list while still being the preselected value.
  const selectedIsBlocked = blocked.some((pack) => pack.id === selected);
  // A single-run project has no sub-projects to split, and a project without a
  // scan has nothing to plan from. In both cases the preview would be noise, so
  // the dialog stays exactly as it was.
  const preview =
    plan != null && plan.composite && plan.scan_available ? plan : null;

  return (
    <Modal
      open={open}
      title="Generate documentation"
      description={`Choose what kind of documentation to write for ${projectLabel}. You can generate again later with a different choice.`}
      size="lg"
      onClose={onCancel}
      footer={
        <>
          <Button variant="ghost" onClick={onCancel} disabled={submitting}>
            Cancel
          </Button>
          <Button
            onClick={confirm}
            isLoading={submitting}
            // Never gated on the plan: the plan is advisory, and a project that
            // cannot be planned still has to be generatable.
            disabled={packs == null || selectedIsBlocked}
          >
            <FileText className="h-3.5 w-3.5" />{" "}
            {preview ? generateButtonLabel(preview.will_generate) : "Start generating"}
          </Button>
        </>
      }
    >
      {preview && <GenerationPlanPreview plan={preview} />}

      {/* Non-blocking on purpose. The operator loses the cost estimate, not the
          ability to generate. */}
      {planError && (
        <p className="mb-4 flex items-start gap-2 rounded-md border border-border bg-muted/30 p-2.5 text-xs text-muted-foreground">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          <span>
            Could not work out which sub-projects this run would regenerate, so
            the cost preview is not shown. Generating still works. ({planError})
          </span>
        </p>
      )}
      {planLoading && !plan && !planError && (
        <p className="mb-4 flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> Working out
          what this run would regenerate&hellip;
        </p>
      )}

      {packs == null && !loadError ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading the available
          documentation packs…
        </p>
      ) : loadError ? (
        <div className="space-y-2">
          <p className="flex items-start gap-2 text-sm text-destructive">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>Could not load the documentation packs: {loadError}</span>
          </p>
          <p className="text-sm text-muted-foreground">
            You can still generate standard documentation without a pack.
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          <PackOption
            title="Standard documentation"
            subtitle="No pack. Writes the default documentation set for this platform."
            checked={selected === null}
            onSelect={() => setSelected(null)}
          />
          {usable.map((pack) => (
            <PackOption
              key={pack.id}
              title={pack.name}
              subtitle={pack.description?.trim() || packSubtitle(pack)}
              badge={pack.id === currentPackId ? "Current choice" : undefined}
              checked={selected === pack.id}
              onSelect={() => setSelected(pack.id)}
            />
          ))}
          {usable.length === 0 && (
            <p className="px-1 text-sm text-muted-foreground">
              No documentation packs are available yet, so this run will write
              standard documentation.
            </p>
          )}
          {blocked.length > 0 && (
            <div className="space-y-2 pt-2">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Not available
              </p>
              {blocked.map((pack) => (
                <PackOption
                  key={pack.id}
                  title={pack.name}
                  subtitle={
                    pack.blocked_reason?.trim() ||
                    "This pack is not certified to generate documentation."
                  }
                  checked={selected === pack.id}
                  disabled
                  onSelect={() => undefined}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {selectedIsBlocked && (
        <p className="mt-3 text-sm text-destructive">
          This project&apos;s saved pack can no longer generate documentation.
          Pick another option above.
        </p>
      )}

      {packs != null && !loadError && (
        <label className="mt-4 flex items-start gap-2 text-sm text-muted-foreground">
          <input
            type="checkbox"
            className="mt-0.5 h-4 w-4 rounded border-input"
            checked={remember}
            onChange={(e) => setRemember(e.target.checked)}
          />
          <span>
            Remember this for {projectLabel}, so the next generation starts with
            the same choice.
          </span>
        </label>
      )}
    </Modal>
  );
}

/**
 * Primary button label, named after the size of the run.
 *
 * `Start generating` on a six-sub-project project reads the same whether it
 * costs one sub-project or six, which is exactly the thing the operator needs to
 * know before pressing it. Zero keeps the neutral label: "Generate 0
 * sub-projects" describes a button that does nothing, and the run still rebuilds
 * the shared cross-project views, so the count belongs in the preview text
 * rather than on the button.
 */
function generateButtonLabel(willGenerate: number): string {
  if (willGenerate <= 0) return "Start generating";
  return `Generate ${willGenerate} ${willGenerate === 1 ? "sub-project" : "sub-projects"}`;
}

/** Server vocabulary -> one short operator-facing phrase. */
const REASON_LABELS: Record<string, string> = {
  new: "new sub-project",
  changed: "code changed",
  unchanged: "unchanged",
  no_published_docs: "no published documents",
  fingerprint_unavailable: "no recorded fingerprint",
  shared_artifact_changed: "shared artifact changed",
  flat_layout_adoption: "adopting the existing layout",
};

function reasonLabel(reason: string): string {
  // Unknown reasons are shown verbatim rather than hidden: the server owns this
  // vocabulary and may extend it, and a blank is worse than a raw token.
  return REASON_LABELS[reason] ?? reason.replace(/_/g, " ");
}

function formatGeneratedAt(value: string | null | undefined): string {
  if (!value) return "never generated";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "never generated";
  return `generated ${date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  })}`;
}

/**
 * What this run would actually do, per sub-project.
 *
 * The split is the headline because it is the money question: a project with six
 * sub-projects where one changed pays for one, and nothing in the old dialog
 * said so. Reused sub-projects are listed by name rather than counted, so the
 * operator can check that the one they changed is on the generate side -- a
 * count alone cannot be verified against what they know they touched.
 *
 * No figure here is computed in the browser. Everything rendered comes from the
 * server's own dry run of the skip predicate the real run uses.
 */
function GenerationPlanPreview({ plan }: { plan: GenerationPlan }) {
  const generating = plan.partitions.filter((part) => part.action === "generate");
  const reusing = plan.partitions.filter((part) => part.action === "reuse");

  return (
    <div className="mb-4 rounded-lg border border-border bg-muted/30 p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-sm font-semibold text-foreground">
          {plan.will_generate} of {plan.partition_count}{" "}
          {plan.partition_count === 1 ? "sub-project" : "sub-projects"} will be
          regenerated
        </p>
        <p className="text-xs text-muted-foreground">
          {plan.will_reuse} reused, so this run does not pay for{" "}
          {plan.will_reuse === 1 ? "it" : "them"}
        </p>
      </div>

      {/* Two columns so generate and reuse are read as one comparison rather
          than two lists that happen to be adjacent. */}
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <div>
          <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-foreground">
            <FileText className="h-3.5 w-3.5" aria-hidden /> Will generate (
            {plan.will_generate})
          </p>
          {generating.length === 0 ? (
            <p className="mt-1.5 text-xs text-muted-foreground">
              No sub-project needs regenerating. Only the shared cross-project
              views are rebuilt.
            </p>
          ) : (
            <ul className="mt-1.5 max-h-40 space-y-1 overflow-y-auto">
              {generating.map((part) => (
                <li key={part.name} className="text-xs">
                  <span className="block truncate font-medium text-foreground">
                    {part.name}
                  </span>
                  <span className="text-muted-foreground">
                    {part.unit_count} {part.unit_count === 1 ? "unit" : "units"} ·{" "}
                    {reasonLabel(part.reason)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div>
          <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            <RotateCcw className="h-3.5 w-3.5" aria-hidden /> Will be reused (
            {plan.will_reuse})
          </p>
          {reusing.length === 0 ? (
            <p className="mt-1.5 text-xs text-muted-foreground">
              Nothing is reused in this run.
            </p>
          ) : (
            <ul className="mt-1.5 max-h-40 space-y-1 overflow-y-auto">
              {reusing.map((part) => (
                <li key={part.name} className="text-xs">
                  <span className="block truncate font-medium text-foreground">
                    {part.name}
                  </span>
                  <span className="text-muted-foreground">
                    {part.doc_count} {part.doc_count === 1 ? "document" : "documents"} ·{" "}
                    {formatGeneratedAt(part.last_generated_at)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {/* The stated reason every sub-project regenerates anyway. Without it the
          preview would show six-of-six and look like a bug. */}
      {plan.global_invalidation && (
        <p className="mt-3 flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/[0.08] p-2.5 text-xs text-amber-700 dark:text-amber-400">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          <span>{plan.global_invalidation.detail}</span>
        </p>
      )}

      {plan.notes.length > 0 && (
        <ul className="mt-3 space-y-1">
          {plan.notes.map((note) => (
            <li
              key={note}
              className="flex items-start gap-1.5 text-xs text-muted-foreground"
            >
              <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
              <span>{note}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function PackOption({
  title,
  subtitle,
  badge,
  checked,
  disabled = false,
  onSelect,
}: {
  title: string;
  subtitle: string;
  badge?: string;
  checked: boolean;
  disabled?: boolean;
  onSelect: () => void;
}) {
  return (
    <label
      className={cn(
        "flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors",
        disabled
          ? "cursor-not-allowed border-border bg-muted/30 opacity-70"
          : checked
            ? "border-primary bg-primary/[0.04]"
            : "border-border hover:border-primary/40 hover:bg-muted/30",
      )}
    >
      <input
        type="radio"
        name="generate-pack"
        className="mt-1 h-4 w-4"
        checked={checked}
        disabled={disabled}
        onChange={onSelect}
      />
      <span className="min-w-0">
        <span className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-semibold text-foreground">{title}</span>
          {badge && (
            <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
              {badge}
            </span>
          )}
        </span>
        <span className="mt-0.5 block text-xs text-muted-foreground">{subtitle}</span>
      </span>
    </label>
  );
}

/** Apply a dialog choice: persist it when asked, and report the pack to use. */
export async function applyGenerateChoice(
  projectId: string,
  choice: GenerateChoice,
): Promise<number | null> {
  if (choice.remember) {
    // Persisting is best-effort relative to generating: failing to save a
    // preference must not block the run the user actually asked for.
    try {
      await selectProjectPack(projectId, choice.packId);
    } catch {
      // The run below still uses the choice for this generation.
    }
  }
  return choice.packId;
}
