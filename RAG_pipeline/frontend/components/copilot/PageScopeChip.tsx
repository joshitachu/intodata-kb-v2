"use client";

import { Network } from "lucide-react";
import { ScopeChip } from "./ScopeChip";
import { describeTopologyLevel } from "@/lib/topologyTargets";
import type { PageScope } from "@/types/topology";

const SURFACE_LABEL: Record<PageScope["surface"], string> = {
  topology: "Topology",
};

interface PageScopeChipProps {
  scope: PageScope;
  className?: string;
}

/**
 * Records where a question was asked, e.g. "Topology › tJob_OrderSync".
 *
 * One thread now collects questions from several surfaces, so this is what
 * makes the history readable after the fact -- legibility beats subtlety here.
 * `page_scope` carries ids (not names) for integration levels, so those render
 * as the level kind rather than an unreadable hash.
 */
export function PageScopeChip({ scope, className }: PageScopeChipProps) {
  return (
    <ScopeChip
      parts={[SURFACE_LABEL[scope.surface] ?? scope.surface, describeTopologyLevel(scope.level)]}
      separator="›"
      icon={Network}
      className={className}
      label="Asked from"
    />
  );
}
