"use client";

import { useEffect, useState } from "react";
import { AlertCircle, FileText, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { listPacks, packSubtitle, selectProjectPack, type Pack } from "@/lib/packs";

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
  projectLabel: string;
  /** The project's currently saved pack, preselected. */
  currentPackId: number | null;
  onCancel: () => void;
  onConfirm: (choice: GenerateChoice) => void | Promise<void>;
}

export function GeneratePackDialog({
  open,
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
            disabled={packs == null || selectedIsBlocked}
          >
            <FileText className="h-3.5 w-3.5" /> Start generating
          </Button>
        </>
      }
    >
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
