"use client";

import { Check, Fingerprint, LockKeyhole } from "lucide-react";
import type { GovernedPackActivation, GovernedPackSelection, GovernancePack, GovernancePolicy, StylePack } from "@/types";

interface StackInspectorProps {
  policy: GovernancePolicy;
  library: GovernancePack[];
  styles: StylePack[];
  selection: GovernedPackSelection;
  activation: GovernedPackActivation | null;
  onChange: (selection: GovernedPackSelection) => void;
}

function toggle(values: string[], id: string): string[] {
  return values.includes(id) ? values.filter((value) => value !== id) : [...values, id].sort();
}

function contributionParts(pack: GovernancePack): string[] {
  const parts: string[] = [];
  if (pack.workflow_labels.length) parts.push(`workflows ${pack.workflow_labels.join(", ")}`);
  if (pack.entity_labels.length) parts.push(`entities ${pack.entity_labels.join(", ")}`);
  if (pack.domains.length) parts.push(`domains ${pack.domains.join(", ")}`);
  if (pack.includes.length) parts.push(`inherits ${pack.includes.join(", ")}`);
  if (pack.documentation_provider_ref) parts.push(`documentation ${pack.documentation_provider_ref}`);
  if (!parts.length && pack.phase === "style") parts.push("presentation settings only");
  return parts;
}

export function StackInspector({ policy, library, styles, selection, activation, onChange }: StackInspectorProps) {
  const foundation = library.filter((pack) => pack.phase === "foundation");
  const functional = library.filter((pack) => pack.phase === "functional" && (policy.allowed_functional_pack_ids.length === 0 || policy.allowed_functional_pack_ids.includes(pack.id)));
  const hashes = new Map(activation?.snapshot.resolved_packs.map((pack) => [pack.id, pack.content_hash]) ?? []);
  const requiredFoundationId = policy.required_certified_pack_ids.find((id) => foundation.some((pack) => pack.id === id)) ?? foundation[0]?.id;

  function setFunctional(id: string) {
    onChange({ ...selection, foundation_pack_ids: requiredFoundationId ? [requiredFoundationId] : [], functional_pack_ids: toggle(selection.functional_pack_ids, id) });
  }
  function setStyle(id: string) {
    onChange({ ...selection, foundation_pack_ids: requiredFoundationId ? [requiredFoundationId] : [], style_pack_ids: toggle(selection.style_pack_ids, id) });
  }

  return (
    <section className="space-y-5" aria-labelledby="effective-stack-heading">
      <div>
        <h2 id="effective-stack-heading" className="text-base font-semibold">Effective stack</h2>
        <p className="mt-1 text-sm text-muted-foreground">The server validates this selection and compiles its inherited policy. Foundation is mandatory; Functional modules combine with it; Style is presentation-only.</p>
      </div>
      <SelectionLayer title="1. Foundation / Core" description="Mandatory, certified, immutable" packs={foundation} selectedIds={requiredFoundationId ? [requiredFoundationId] : []} hashes={hashes} required />
      <SelectionLayer title="2. Functional / Analysis" description="Certified modules permitted by organization policy" packs={functional} selectedIds={selection.functional_pack_ids} hashes={hashes} onToggle={setFunctional} />
      <SelectionLayer title="3. Style / Presentation" description="Private and project presentation overlays only" packs={styles.map((style) => style.pack)} selectedIds={selection.style_pack_ids} hashes={hashes} onToggle={setStyle} styles={styles} />
      {activation && <p className="flex items-center gap-1.5 border-t pt-3 text-xs text-muted-foreground"><Fingerprint className="h-3.5 w-3.5" aria-hidden />Activation snapshot {activation.snapshot.snapshot_hash}</p>}
    </section>
  );
}

function SelectionLayer({ title, description, packs, selectedIds, hashes, onToggle, required = false, styles }: {
  title: string;
  description: string;
  packs: GovernancePack[];
  selectedIds: string[];
  hashes: Map<string, string>;
  onToggle?: (id: string) => void;
  required?: boolean;
  styles?: StylePack[];
}) {
  return (
    <div className="border-y border-border" aria-label={title}>
      <div className="py-3"><h3 className="text-sm font-semibold">{title}</h3><p className="mt-0.5 text-xs text-muted-foreground">{description}</p></div>
      <div className="divide-y divide-border">
        {packs.map((pack) => {
          const selected = selectedIds.includes(pack.id);
          const style = styles?.find((item) => item.pack.id === pack.id);
          const contributions = contributionParts(pack);
          return <label key={pack.id} className="flex cursor-pointer items-start gap-3 py-3">
            <input type={required ? "radio" : "checkbox"} checked={selected} disabled={required} onChange={() => onToggle?.(pack.id)} className="mt-0.5 h-4 w-4 accent-primary disabled:opacity-100" />
            <span className="min-w-0 flex-1">
              <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm font-medium">{pack.name} <span className="text-xs font-normal text-muted-foreground">v{pack.version}</span>{selected && <Check className="h-3.5 w-3.5 text-emerald-700" aria-label="Active" />}</span>
              <span className="mt-1 block text-xs text-muted-foreground">{pack.owner} owner · {pack.trust} · {pack.mutability}</span>
              {pack.workflow_descriptions[0] && <span className="mt-1 block text-xs text-foreground/80">{pack.workflow_descriptions[0]}</span>}
              <span className="mt-1 block text-xs text-muted-foreground"><span className="font-medium text-foreground">Adds:</span> {contributions.length ? contributions.join(" · ") : "governance defaults"}</span>
              {style && <span className="mt-1 block text-xs text-muted-foreground">{style.review_status === "draft" ? "Draft" : style.review_status} · {style.promotion_eligible ? "Promotion-eligible; PR review required" : "Not promotion-eligible; PR review required"}</span>}
              <span className="mt-1 block break-all font-mono text-[11px] text-muted-foreground">Hash {hashes.get(pack.id) ?? pack.content_hash}</span>
              {required && <span className="mt-1 flex items-start gap-1 text-xs text-muted-foreground"><LockKeyhole className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />Inherited organization requirement; it cannot be removed.</span>}
            </span>
          </label>;
        })}
        {!packs.length && <p className="py-3 text-sm text-muted-foreground">No accessible Packs in this layer.</p>}
      </div>
    </div>
  );
}
