"use client";

import Link from "next/link";
import { Network } from "lucide-react";
import { cn } from "@/lib/utils";
import { describeTopologyLevel, parseTopologyTargets } from "@/lib/topologyTargets";
import { topologyHref } from "@/lib/topologyUrl";

export interface TopologyTargetLinksProps {
  /** Raw `structured_answer.topology_targets`, validated here. */
  payload: unknown;
  /** Project whose topology to open. */
  projectId?: string | null;
  /** Carried into the link so the thread survives the jump. */
  conversationId?: number | null;
  className?: string;
}

/**
 * An answer's topology targets on a page with no canvas.
 *
 * The docked copilot turns these into chips that highlight and drill in place;
 * here there is nothing to highlight, so each becomes a link that opens
 * `/topology` already at that level. Before this they were parsed and dropped,
 * which meant the most actionable part of a topology answer was invisible
 * anywhere except the one page that already showed it.
 *
 * No resolvability check, deliberately: this page has no graph to validate
 * against, and guessing would either hide working links or fake dead ones. The
 * topology page validates the level it is handed and falls back to the project
 * level when it cannot place it (see `lib/topologyUrl.ts`).
 */
export function TopologyTargetLinks({
  payload,
  projectId,
  conversationId,
  className,
}: TopologyTargetLinksProps) {
  const { targets } = parseTopologyTargets(payload);
  if (targets.length === 0) return null;

  return (
    <div
      className={cn("flex flex-wrap gap-2 pt-2", className)}
      aria-label="Open in topology"
    >
      {targets.map((target) => {
        const detail = describeTopologyLevel(target.level);
        return (
          <Link
            key={`${target.label}:${detail}`}
            href={topologyHref({ level: target.level, projectId, conversationId })}
            data-topology-link="true"
            className={cn(
              "inline-flex max-w-full items-center gap-1.5 rounded-md border px-2.5 py-1.5",
              "text-xs font-medium text-primary transition-colors hover:bg-muted",
            )}
          >
            <Network className="h-3 w-3 shrink-0" aria-hidden />
            <span className="truncate">{target.label}</span>
            {detail !== target.label && (
              <span className="shrink-0 text-muted-foreground">{detail}</span>
            )}
          </Link>
        );
      })}
    </div>
  );
}
