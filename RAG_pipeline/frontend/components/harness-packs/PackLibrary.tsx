"use client";

import { Fingerprint, LockKeyhole, ShieldCheck } from "lucide-react";
import type { GovernancePack } from "@/types";

interface PackLibraryProps {
  packs: GovernancePack[];
}

function score(value: number | null) {
  return value === null ? "Not published" : `${Math.round(value * 100)}%`;
}

function contributionParts(pack: GovernancePack): string[] {
  const parts: string[] = [];
  if (pack.workflow_labels.length) parts.push(`Workflows: ${pack.workflow_labels.join(", ")}`);
  if (pack.entity_labels.length) parts.push(`Entities: ${pack.entity_labels.join(", ")}`);
  if (pack.domains.length) parts.push(`Domains: ${pack.domains.join(", ")}`);
  if (pack.includes.length) parts.push(`Inherits: ${pack.includes.join(", ")}`);
  if (pack.documentation_provider_ref) parts.push(`Documentation: ${pack.documentation_provider_ref}`);
  return parts;
}

export function PackLibrary({ packs }: PackLibraryProps) {
  const foundation = packs.filter((pack) => pack.phase === "foundation");
  const functional = packs.filter((pack) => pack.phase === "functional");

  return (
    <section className="space-y-5" aria-labelledby="certified-library-heading">
      <div>
        <h2 id="certified-library-heading" className="text-base font-semibold">Certified library</h2>
        <p className="mt-1 text-sm text-muted-foreground">Foundation and Functional Packs are Git and PR controlled. Their policy, evidence, capability, and semantic content cannot be overridden here.</p>
      </div>
      <PackLayer title="Foundation / Core" packs={foundation} required />
      <PackLayer title="Functional / Analysis" packs={functional} />
    </section>
  );
}

function PackLayer({ title, packs, required = false }: { title: string; packs: GovernancePack[]; required?: boolean }) {
  return (
    <div className="border-y border-border" aria-label={title}>
      <div className="flex items-center gap-2 py-3 text-sm font-semibold">
        <ShieldCheck className="h-4 w-4 text-emerald-700" aria-hidden />
        {title}
        {required && <span className="text-xs font-normal text-muted-foreground">Required for governed workflows</span>}
      </div>
      <div className="divide-y divide-border">
        {packs.map((pack) => {
          const contributions = contributionParts(pack);
          return <article key={pack.id} className="grid gap-2 py-3 sm:grid-cols-[minmax(0,1fr)_minmax(14rem,auto)] sm:items-start">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <h3 className="text-sm font-medium">{pack.name}</h3>
                <span className="text-xs text-muted-foreground">v{pack.version}</span>
              </div>
              <p className="mt-1 text-xs text-muted-foreground">{pack.owner} owner · {pack.authority.replaceAll("_", " ")} · {pack.trust}</p>
              {pack.workflow_descriptions[0] && <p className="mt-2 text-sm text-foreground/80">{pack.workflow_descriptions[0]}</p>}
              <p className="mt-2 text-xs text-muted-foreground"><span className="font-medium text-foreground">Adds:</span> {contributions.length ? contributions.join(" · ") : "Governance and inherited workflow defaults"}</p>
              <p className="mt-2 flex items-start gap-1.5 text-xs text-muted-foreground"><LockKeyhole className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />{pack.blocked_override_rules.join("; ")}</p>
            </div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-muted-foreground sm:text-right">
              <span>Quality {score(pack.quality_score)}</span>
              <span>Evaluation {score(pack.evaluation_score)}</span>
              <span className="col-span-2 flex min-w-0 items-start gap-1 font-mono text-[11px] sm:max-w-sm sm:justify-end"><Fingerprint className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden /><span className="break-all">{pack.content_hash}</span></span>
            </div>
          </article>
        })}
        {!packs.length && <p className="py-3 text-sm text-muted-foreground">No certified Packs are available in this layer.</p>}
      </div>
    </div>
  );
}
