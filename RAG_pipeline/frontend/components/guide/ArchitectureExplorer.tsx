"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  ChevronRight,
  Database,
  GitBranch,
  type LucideIcon,
  Maximize2,
  Minimize2,
  ScanSearch,
  Sparkles,
} from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Zoomable model of the platform: four blocks, drilled two levels deep.
 *
 * The written guide already explains each subsystem in prose, but prose cannot
 * answer "where does this sit relative to everything else" — the question new
 * engineers and evaluators actually ask first. Zooming out gives the shape;
 * zooming in gives the mechanism, without opening a diagram in another tab.
 *
 * Content is data, not markup, so a new subsystem is one object rather than a
 * new layout.
 */

interface Detail {
  id: string;
  title: string;
  /** One line: what this part does. */
  summary: string;
  /** Concrete facts. Kept short — this is a map, not a manual. */
  facts: string[];
  /** Where in the product this becomes visible, if anywhere. */
  seeItAt?: { label: string; href: string };
}

interface Subsystem {
  id: string;
  label: string;
  tagline: string;
  details: Detail[];
}

interface Block {
  id: string;
  label: string;
  icon: LucideIcon;
  /** Shown at the zoomed-out level. */
  role: string;
  subsystems: Subsystem[];
}

const BLOCKS: Block[] = [
  {
    id: "sources",
    label: "Sources",
    icon: GitBranch,
    role: "Where raw material comes from, and how it gets in safely.",
    subsystems: [
      {
        id: "code",
        label: "Code and archives",
        tagline: "Repositories and ZIP uploads, per project.",
        details: [
          {
            id: "connect",
            title: "Connecting a source",
            summary:
              "A project points at one source: a GitHub App installation, a token-authenticated repository, or an uploaded archive.",
            facts: [
              "Credentials are encrypted at rest and never sent to a language model.",
              "Connecting is separate from scanning on purpose — nothing is read until you ask for it.",
              "A GitHub App installation avoids per-user tokens going stale when someone leaves.",
            ],
            seeItAt: { label: "Sources", href: "/settings/sources" },
          },
          {
            id: "scanners",
            title: "Platform scanners",
            summary:
              "Each integration platform has its own scanner, because their artefacts are not comparable.",
            facts: [
              "Talend: jobs, contexts, tMap transformations, metadata connections.",
              "Workato: recipes, connectors, triggers and actions.",
              "Lobster: integration workflows.",
              "Generic: any other service or integration repository.",
            ],
          },
        ],
      },
      {
        id: "runtime",
        label: "Runtime signals",
        tagline: "What the integrations actually did, not just what they contain.",
        details: [
          {
            id: "mirror",
            title: "Execution mirror",
            summary:
              "Runtime sources are polled on their own interval and mirrored locally, so history survives retention limits upstream.",
            facts: [
              "Executions, incidents and log lines are stored per environment.",
              "The mirror is what lets a question about a failure be answered later, after the source system has rotated its logs.",
            ],
            seeItAt: { label: "Runtime logs", href: "/admin/logs" },
          },
        ],
      },
    ],
  },
  {
    id: "intelligence",
    label: "Intelligence",
    icon: ScanSearch,
    role: "Turning raw artefacts into reviewed, structured facts.",
    subsystems: [
      {
        id: "scan",
        label: "Scan and extract",
        tagline: "Read the source, emit facts — no interpretation yet.",
        details: [
          {
            id: "facts",
            title: "Technical facts first",
            summary:
              "A scan produces a structured inventory: components, their configuration, and the connections between them.",
            facts: [
              "Extraction is deterministic. The same source scanned twice yields the same facts.",
              "Facts are cached, so generating documentation again does not mean re-reading the source.",
              "Gaps are recorded rather than guessed at — an unparseable component is reported, not invented.",
            ],
          },
        ],
      },
      {
        id: "generate",
        label: "Generate documentation",
        tagline: "Facts become architecture, component and operations docs.",
        details: [
          {
            id: "packs",
            title: "Documentation packs",
            summary:
              "A pack decides which documents get produced and how they are structured, so output is consistent across projects.",
            facts: [
              "Generation writes to a staging area first; the reviewed result is swapped in on success.",
              "A failed generation leaves the previous documentation untouched.",
              "Generating is not publishing — the docs still have to be pushed to the knowledge base.",
            ],
            seeItAt: { label: "Project Docs", href: "/docs" },
          },
        ],
      },
      {
        id: "topology",
        label: "Topology model",
        tagline: "The same facts, expressed as a navigable landscape.",
        details: [
          {
            id: "levels",
            title: "Systems down to components",
            summary:
              "Topology rolls job-level detail up into systems and integrations, then lets you drill back down.",
            facts: [
              "Each node carries the evidence it was derived from, so a relationship can be justified.",
              "Zooming out is a summary of the same data, not a separate hand-drawn diagram that can go stale.",
            ],
            seeItAt: { label: "Topology", href: "/topology" },
          },
        ],
      },
    ],
  },
  {
    id: "knowledge",
    label: "Knowledge base",
    icon: Database,
    role: "Making everything searchable, scoped, and citable.",
    subsystems: [
      {
        id: "ingest",
        label: "Ingestion",
        tagline: "Parse, chunk, embed, store — once, with structure preserved.",
        details: [
          {
            id: "chunking",
            title: "Structure-aware chunking",
            summary:
              "Documents are split along their own structure rather than at arbitrary character counts.",
            facts: [
              "Markdown chunks keep their heading path, section and unit.",
              "Office formats keep slide and sheet boundaries.",
              "Oversized blocks fall back to overlapping token chunks so nothing is silently dropped.",
            ],
          },
          {
            id: "vectors",
            title: "Two retrieval signals",
            summary:
              "Each chunk is stored with a dense vector for meaning and an optional sparse vector for exact terms.",
            facts: [
              "Dense search finds passages that say the same thing in different words.",
              "Sparse search protects identifiers — job names, table names, error codes.",
              "Scope fields such as project and allowed groups are written by the server and cannot be overridden by a caller.",
            ],
          },
        ],
      },
      {
        id: "revisions",
        label: "Revisions",
        tagline: "Publishing is explicit and reversible.",
        details: [
          {
            id: "active",
            title: "One active revision",
            summary:
              "A prepared revision is not answerable until it is made active, so a half-finished publish cannot change answers.",
            facts: [
              "Replacing a document removes its old chunks before writing new ones.",
              "Citations expose filename, section and revision, so a stale answer is diagnosable rather than mysterious.",
            ],
          },
        ],
      },
    ],
  },
  {
    id: "surfaces",
    label: "Surfaces",
    icon: Sparkles,
    role: "Where people actually work — and what governs their access.",
    subsystems: [
      {
        id: "chat",
        label: "Chat and Deep Dive",
        tagline: "Ask, get a cited answer, follow it to the source.",
        details: [
          {
            id: "retrieval",
            title: "Filter, retrieve, rerank, answer",
            summary:
              "Authorization is part of the vector query, so retrieval can only ever see evidence you are entitled to.",
            facts: [
              "Candidates from both search signals are fused, then re-scored against your question.",
              "Weak matches are dropped rather than used as filler.",
              "If the reranker is unavailable, retrieval order is used rather than failing the request.",
            ],
            seeItAt: { label: "Chat", href: "/" },
          },
        ],
      },
      {
        id: "access",
        label: "Access control",
        tagline: "Three layers: role, project assignment, ownership.",
        details: [
          {
            id: "layers",
            title: "What each layer decides",
            summary:
              "Roles decide what you can operate. Project assignments decide what you can see. Ownership decides which project you can administer.",
            facts: [
              "Owner authority is a persisted flag, not a role, so it cannot be handed out by inviting someone.",
              "Access approval records the role the member arrives with, and the grant is audited.",
              "Every retrieval query is filtered by the requester's projects and groups.",
            ],
            seeItAt: {
              label: "Organization",
              href: "/admin/organization?tab=access",
            },
          },
        ],
      },
    ],
  },
];

type Zoom =
  | { level: 1 }
  | { level: 2; blockId: string }
  | { level: 3; blockId: string; subsystemId: string };

const LEVEL_LABEL = ["", "Platform", "Subsystems", "Mechanism"];

export function ArchitectureExplorer() {
  const [zoom, setZoom] = useState<Zoom>({ level: 1 });

  const block = useMemo(
    () =>
      zoom.level === 1
        ? null
        : BLOCKS.find((candidate) => candidate.id === zoom.blockId) ?? null,
    [zoom],
  );
  const subsystem = useMemo(
    () =>
      zoom.level === 3
        ? block?.subsystems.find((s) => s.id === zoom.subsystemId) ?? null
        : null,
    [zoom, block],
  );

  const zoomOut = useCallback(() => {
    setZoom((current) => {
      if (current.level === 3) {
        return { level: 2, blockId: current.blockId };
      }
      return { level: 1 };
    });
  }, []);

  // Escape zooms out one level rather than exiting, matching how the topology
  // canvas behaves — leaving the explorer entirely on one keypress loses the
  // reader's place.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape" && zoom.level > 1) {
        event.stopPropagation();
        zoomOut();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [zoom.level, zoomOut]);

  return (
    <div className="overflow-hidden rounded-xl border border-border bg-card">
      {/* Breadcrumb + zoom controls */}
      <div className="flex flex-wrap items-center gap-2 border-b border-border bg-muted/40 px-4 py-3">
        <span className="text-[10px] font-bold uppercase tracking-[0.14em] text-muted-foreground">
          {LEVEL_LABEL[zoom.level]}
        </span>
        <ChevronRight className="h-3 w-3 text-muted-foreground/60" aria-hidden />
        <nav aria-label="Architecture breadcrumb" className="flex items-center gap-1.5 text-xs">
          <button
            type="button"
            onClick={() => setZoom({ level: 1 })}
            className={cn(
              "rounded px-1.5 py-0.5 font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
              zoom.level === 1
                ? "text-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            Whole platform
          </button>
          {block && (
            <>
              <ChevronRight className="h-3 w-3 text-muted-foreground/60" aria-hidden />
              <button
                type="button"
                onClick={() => setZoom({ level: 2, blockId: block.id })}
                className={cn(
                  "rounded px-1.5 py-0.5 font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
                  zoom.level === 2
                    ? "text-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {block.label}
              </button>
            </>
          )}
          {subsystem && (
            <>
              <ChevronRight className="h-3 w-3 text-muted-foreground/60" aria-hidden />
              <span className="px-1.5 py-0.5 font-medium text-foreground">
                {subsystem.label}
              </span>
            </>
          )}
        </nav>

        <div className="ml-auto flex items-center gap-1">
          <button
            type="button"
            onClick={zoomOut}
            disabled={zoom.level === 1}
            className="inline-flex h-7 items-center gap-1.5 rounded-md border border-border bg-card px-2 text-[11px] font-semibold text-foreground transition-colors hover:bg-muted/60 disabled:opacity-40 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
          >
            <Minimize2 className="h-3 w-3" aria-hidden />
            Zoom out
          </button>
          <span className="hidden text-[10px] text-muted-foreground sm:inline">
            or press Esc
          </span>
        </div>
      </div>

      <div className="p-4 sm:p-5">
        {zoom.level === 1 && (
          <div key="l1" className="animate-step-enter">
            <p className="mb-4 text-sm leading-6 text-muted-foreground">
              Four blocks, left to right. Evidence flows from Sources through
              Intelligence into the Knowledge base, and Surfaces is where people
              read it back. Select a block to zoom in.
            </p>
            <div className="grid gap-3 lg:grid-cols-4">
              {BLOCKS.map((candidate, position) => {
                const Icon = candidate.icon;
                return (
                  <button
                    key={candidate.id}
                    type="button"
                    onClick={() => setZoom({ level: 2, blockId: candidate.id })}
                    className="group relative rounded-lg border border-border bg-muted/30 p-4 text-left transition-all hover:border-primary/40 hover:bg-primary/[0.05] focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
                  >
                    <div className="flex items-center justify-between">
                      <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10 text-primary">
                        <Icon className="h-4 w-4" aria-hidden />
                      </span>
                      <span className="text-[10px] font-bold text-muted-foreground/60">
                        0{position + 1}
                      </span>
                    </div>
                    <p className="mt-3 text-sm font-semibold text-foreground">
                      {candidate.label}
                    </p>
                    <p className="mt-1 text-xs leading-5 text-muted-foreground">
                      {candidate.role}
                    </p>
                    <span className="mt-3 inline-flex items-center gap-1 text-[11px] font-semibold text-primary opacity-0 transition-opacity group-hover:opacity-100">
                      <Maximize2 className="h-3 w-3" aria-hidden />
                      {candidate.subsystems.length} subsystem
                      {candidate.subsystems.length === 1 ? "" : "s"}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {zoom.level === 2 && block && (
          <div key={`l2-${block.id}`} className="animate-step-enter">
            <div className="mb-4 flex items-start gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                <block.icon className="h-4 w-4" aria-hidden />
              </span>
              <div>
                <h3 className="text-base font-semibold text-foreground">
                  {block.label}
                </h3>
                <p className="mt-0.5 text-sm leading-6 text-muted-foreground">
                  {block.role}
                </p>
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {block.subsystems.map((candidate) => (
                <button
                  key={candidate.id}
                  type="button"
                  onClick={() =>
                    setZoom({
                      level: 3,
                      blockId: block.id,
                      subsystemId: candidate.id,
                    })
                  }
                  className="group rounded-lg border border-border bg-muted/30 p-4 text-left transition-all hover:border-primary/40 hover:bg-primary/[0.05] focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
                >
                  <p className="text-sm font-semibold text-foreground">
                    {candidate.label}
                  </p>
                  <p className="mt-1 text-xs leading-5 text-muted-foreground">
                    {candidate.tagline}
                  </p>
                  <span className="mt-3 inline-flex items-center gap-1 text-[11px] font-semibold text-primary">
                    How it works
                    <ArrowRight
                      className="h-3 w-3 transition-transform group-hover:translate-x-0.5"
                      aria-hidden
                    />
                  </span>
                </button>
              ))}
            </div>
          </div>
        )}

        {zoom.level === 3 && block && subsystem && (
          <div key={`l3-${subsystem.id}`} className="animate-step-enter">
            <div className="mb-4">
              <h3 className="text-base font-semibold text-foreground">
                {subsystem.label}
              </h3>
              <p className="mt-0.5 text-sm leading-6 text-muted-foreground">
                {subsystem.tagline}
              </p>
            </div>

            <div className="space-y-3">
              {subsystem.details.map((detail) => (
                <article
                  key={detail.id}
                  className="rounded-lg border border-border bg-muted/30 p-4"
                >
                  <h4 className="text-sm font-semibold text-foreground">
                    {detail.title}
                  </h4>
                  <p className="mt-1 text-sm leading-6 text-muted-foreground">
                    {detail.summary}
                  </p>
                  <ul className="mt-3 space-y-1.5">
                    {detail.facts.map((fact) => (
                      <li
                        key={fact}
                        className="flex gap-2 text-xs leading-5 text-muted-foreground"
                      >
                        <span
                          className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-primary/60"
                          aria-hidden
                        />
                        {fact}
                      </li>
                    ))}
                  </ul>
                  {detail.seeItAt && (
                    <a
                      href={detail.seeItAt.href}
                      className="mt-3 inline-flex items-center gap-1.5 rounded-md border border-border bg-card px-2.5 py-1 text-[11px] font-semibold text-foreground transition-colors hover:bg-muted/60"
                    >
                      Open {detail.seeItAt.label}
                      <ArrowRight className="h-3 w-3" aria-hidden />
                    </a>
                  )}
                </article>
              ))}
            </div>

            <button
              type="button"
              onClick={zoomOut}
              className="mt-4 inline-flex items-center gap-1.5 text-xs font-semibold text-primary hover:underline"
            >
              <ArrowLeft className="h-3 w-3" aria-hidden />
              Back to {block.label}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
