"use client";

import Image from "next/image";
import {
  ArrowLeft,
  ArrowRight,
  BookOpen,
  CheckCircle2,
  Clock3,
  Database,
  ExternalLink,
  FileCode2,
  GitBranch,
  Info,
  Layers3,
  LockKeyhole,
  Menu,
  Route,
  ScanSearch,
  Search,
  ShieldCheck,
  Sparkles,
  Users,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { cn } from "@/lib/utils";
import { useOnboardingContext } from "@/contexts/OnboardingContext";
import { usePermissions } from "@/hooks/usePermissions";
import { tourFor } from "@/components/tour/tours";
import { applicableSteps } from "@/components/tour/types";
import type { Persona } from "@/types/onboarding";
import { ArchitectureExplorer } from "./ArchitectureExplorer";

/**
 * When this page's content was last checked against the product.
 *
 * A single constant instead of the date typed into four places: the previous
 * version had them spelled out separately, so three of them were guaranteed to
 * go stale the first time someone updated one. Update this on content review.
 */
const LAST_VERIFIED = "27 August 2026";
const LAST_VERIFIED_SHORT = "· 27 Aug 2026";

/**
 * Which edition of the guide a section belongs to.
 *
 * `/guide` used to render every chapter to every reader, so a member looking up
 * how to ask a question also scrolled past Organization, Sources, Observability
 * and the Admin runbook -- four chapters about controls they cannot open. The
 * two editions answer different questions: "how do I use this" versus "how do I
 * run this".
 */
type Edition = "using" | "running";

const EDITIONS: Record<Edition, { label: string; blurb: string }> = {
  using: {
    label: "Using the app",
    blurb: "Asking questions, reading evidence, and finding your way around.",
  },
  running: {
    label: "Running the workspace",
    blurb: "Everything in Using the app, plus access, sources and operations.",
  },
};

type NavItem = {
  id: string;
  label: string;
  keywords?: string;
  summary: string;
  /**
   * Present only in the "running" edition. Paired with `permission` so a
   * chapter is hidden both from members and from an admin who happens to lack
   * that specific permission -- the same rule the walkthrough applies.
   */
  runningOnly?: boolean;
  permission?: string;
};

type NavGroup = {
  label: string;
  items: NavItem[];
};

const NAV_GROUPS: NavGroup[] = [
  {
    label: "Start",
    items: [
      { id: "overview", label: "Overview", keywords: "introduction getting started", summary: "What Nexus does and how evidence moves through the platform." },
      { id: "quickstarts", label: "Quickstarts by role", keywords: "developer admin owner project owner", summary: "A focused first path for your responsibilities." },
      { id: "access", label: "Access and sign-in", keywords: "login email code invitation expiry lock", summary: "Invitation, email-code and password sign-in flows." },
      { id: "first-password", label: "Create or change password", keywords: "passwordless current password sessions reset", summary: "First-password, change and administrator-reset behavior." },
      { id: "mfa", label: "Two-factor authentication", keywords: "2fa totp backup codes recovery", summary: "Enrollment, sign-in challenges and safe recovery." },
    ],
  },
  {
    label: "Understand",
    items: [
      { id: "architecture-map", label: "Architecture map", keywords: "zoom diagram subsystem overview interactive explore structure", summary: "Zoom from the whole platform down to how each subsystem works." },
      { id: "concepts", label: "Core concepts", keywords: "organization project source evidence rag pack runtime", summary: "The vocabulary and evidence lifecycle behind Nexus." },
      { id: "roles", label: "Roles and permissions", keywords: "owner admin developer project owner access control rbac", summary: "Organization roles, project ownership and practical authority boundaries." },
      { id: "developer-architecture", label: "Developer architecture", keywords: "ingestion parsing chunk embeddings qdrant retrieval reranking citations", summary: "How source evidence becomes scoped, cited answers." },
    ],
  },
  {
    label: "Workspace",
    items: [
      { id: "chat", label: "Chat and Deep Dive", keywords: "question citations sources attachments slash packs", summary: "Grounded questions, deeper analysis, attachments and citations." },
      { id: "documents", label: "Documents", keywords: "upload evidence rag roles limits delete", summary: "Upload, classify, index and safely remove evidence." },
      { id: "project-docs", label: "Project Docs", keywords: "generated documentation pdf markdown", summary: "Preview and download generated project documentation." },
      { id: "topology", label: "Topology", keywords: "graph systems jobs components lineage health copilot", summary: "Navigate systems, integrations, components, lineage and evidence." },
      { id: "profile", label: "Profile and Security", keywords: "account password mfa", summary: "Maintain your profile, password and authenticator." },
    ],
  },
  {
    label: "Manage",
    items: [
      { id: "sources", label: "Sources and pipeline", keywords: "github zip scan generate push rag webhook", summary: "Connect code, scan facts, generate docs and publish evidence.", runningOnly: true, permission: "manage_projects" },
      { id: "admin-pages", label: "Organization", keywords: "users invites access roles projects reset members", summary: "Operate member access and owned project scope.", runningOnly: true, permission: "manage_users" },
      { id: "observability", label: "Observe and diagnose", keywords: "usage logs runtime traces memory", summary: "Inspect costs, executions, incidents, traces and memory.", runningOnly: true, permission: "view_audit_logs" },
    ],
  },
  {
    label: "Operate",
    items: [
      { id: "workflows", label: "Common workflows", keywords: "steps how to", summary: "End-to-end tasks with prerequisites and expected results." },
      { id: "admin-runbook", label: "Admin runbook", keywords: "daily checklist operations", summary: "Repeatable access, project and evidence checks.", runningOnly: true, permission: "manage_users" },
      { id: "security", label: "Security and recovery", keywords: "forgot password recovery credentials", summary: "Safe responses to password, MFA and credential incidents." },
      { id: "troubleshooting", label: "Troubleshooting", keywords: "errors missing project login", summary: "Diagnose common access, indexing and evidence problems." },
      { id: "glossary", label: "Glossary", keywords: "definitions terminology", summary: "Precise product and security terminology." },
    ],
  },
];

const NAV_ITEMS = NAV_GROUPS.flatMap((group) => group.items);

/** Section ids that only exist in the "running the workspace" edition. */
export const RUNNING_ONLY_SECTIONS = new Set(
  NAV_ITEMS.filter((item) => item.runningOnly).map((item) => item.id),
);

/**
 * Filter the nav to the chapters this reader should see.
 *
 * A chapter needs BOTH the right edition and the permission behind it. Edition
 * alone is not enough: an admin viewing "running" still should not be taught
 * the Organization chapter if they cannot open Organization.
 */
function visibleGroups(
  edition: Edition,
  hasPermission: (codename: string) => boolean,
): NavGroup[] {
  return NAV_GROUPS.map((group) => ({
    ...group,
    items: group.items.filter((item) => {
      if (!item.runningOnly) return true;
      if (edition !== "running") return false;
      return item.permission ? hasPermission(item.permission) : true;
    }),
  })).filter((group) => group.items.length > 0);
}

type GuideRole = "developer" | "project-owner" | "admin" | "owner";

/**
 * Maps the backend-resolved persona onto this page's role vocabulary.
 *
 * The guide predates the shared persona type and uses a hyphenated id in the
 * URL, so the two names are bridged here rather than renaming a parameter
 * people may already have bookmarked.
 */
const GUIDE_ROLE_FOR_PERSONA: Record<Persona, GuideRole> = {
  owner: "owner",
  admin: "admin",
  project_owner: "project-owner",
  developer: "developer",
};

const ROLE_GUIDES: Record<GuideRole, { label: string; mission: string; first: string[]; boundaries: string; pages: Array<{ label: string; href: string }> }> = {
  developer: {
    label: "Developer",
    mission: "Use evidence from assigned projects without administering the organization.",
    first: ["Confirm the intended project is visible.", "Upload or inspect project evidence.", "Ask a cited question in Chat.", "Trace the answer through Docs or Topology."],
    boundaries: "Project visibility comes from assignments. Administrative controls are not part of the supported Developer workflow.",
    pages: [{ label: "Understand the architecture", href: "#developer-architecture" }, { label: "Open Chat", href: "/" }, { label: "Open Topology", href: "/topology" }],
  },
  "project-owner": {
    label: "Project owner",
    mission: "Operate one owned project while retaining your separate organization role.",
    first: ["Confirm ownership and member scope.", "Connect or review the project source.", "Run scan, generation and evidence checks.", "Assign only the members who need the project."],
    boundaries: "Project owner is an assignment flag, not an organization role. It does not grant global user, role or security authority.",
    pages: [{ label: "Open Sources", href: "/settings/sources" }, { label: "Open Projects", href: "/admin/organization?tab=projects" }, { label: "Open Project Docs", href: "/docs" }],
  },
  admin: {
    label: "Admin",
    mission: "Run day-to-day member access and create or operate projects within owned scope.",
    first: ["Review pending access and invitations.", "Create ordinary members or maintain their roles.", "Create a project and become its project owner.", "Check scans, usage and operational evidence."],
    boundaries: "Admins cannot modify protected Owner accounts or change Owner-only role definitions and global security authority.",
    pages: [{ label: "Open Access", href: "/admin/organization?tab=access" }, { label: "Open Members", href: "/admin/organization?tab=members" }, { label: "Open Projects", href: "/admin/organization?tab=projects" }],
  },
  owner: {
    label: "Owner",
    mission: "Protect deployment-wide authority, roles and exceptional recovery paths.",
    first: ["Confirm organization setup and mail delivery.", "Define least-privilege roles.", "Delegate daily work to Admins.", "Review exceptional recovery and global operational evidence."],
    boundaries: "Owner access is protected and should not be shared. Use it for global authority and exceptions, not routine project work.",
    pages: [{ label: "Open Roles", href: "/admin/organization?tab=roles" }, { label: "Open Members", href: "/admin/organization?tab=members" }, { label: "Open Logs", href: "/admin/logs" }],
  },
};

const TOPIC_SUBHEADINGS: Partial<Record<string, Array<{ id: string; label: string }>>> = {
  access: [{ id: "account-invite", label: "Invitation path" }, { id: "email-login", label: "Email-code login" }, { id: "password-login", label: "Password login" }],
  "first-password": [{ id: "password-create", label: "Create first password" }, { id: "password-change", label: "Change password" }, { id: "password-reset", label: "Admin reset" }],
  mfa: [{ id: "mfa-enrol", label: "Enrol MFA" }, { id: "mfa-recovery", label: "Recovery limits" }],
  roles: [{ id: "rbac-layers", label: "Three access layers" }, { id: "role-matrix", label: "Role matrix" }],
  "developer-architecture": [{ id: "developer-ingestion", label: "Ingestion pipeline" }, { id: "developer-retrieval", label: "Retrieval pipeline" }, { id: "developer-debugging", label: "Trust and debugging" }],
  chat: [{ id: "chat-modes", label: "Chat modes" }, { id: "chat-inputs", label: "Inputs and attachments" }, { id: "chat-evidence", label: "Verify evidence" }],
  documents: [{ id: "document-roles", label: "Document roles" }, { id: "document-lifecycle", label: "Upload lifecycle" }, { id: "document-delete", label: "Deletion" }],
  topology: [{ id: "topology-model", label: "Mental model" }, { id: "topology-controls", label: "Controls" }, { id: "topology-evidence", label: "Evidence and Copilot" }],
  sources: [{ id: "source-pipeline", label: "Pipeline" }, { id: "source-controls", label: "Controls and states" }, { id: "source-safety", label: "Safety" }],
  observability: [{ id: "observe-usage", label: "Usage" }, { id: "observe-runtime", label: "Logs and runtime" }, { id: "observe-quality", label: "Traces, benchmark and memory" }],
};

const pageLinkClass =
  "inline-flex h-9 items-center justify-center gap-2 rounded-md border border-border bg-card px-3 text-xs font-semibold text-foreground shadow-elevation-xs transition-colors hover:bg-muted/50 focus:outline-none focus:ring-2 focus:ring-primary/25";

function scrollToSection(id: string) {
  const section = document.getElementById(id);
  if (!section) return;
  section.scrollIntoView?.({ behavior: "smooth", block: "start" });
  window.history.replaceState(null, "", `#${id}`);
}

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section id={id} className="scroll-mt-24 border-b border-border py-12 last:border-b-0">
      <div className="mb-6 flex items-center gap-3">
        <h2 className="text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">{title}</h2>
        <button
          type="button"
          onClick={() => scrollToSection(id)}
          className="text-sm text-muted-foreground/60 transition-colors hover:text-foreground"
          aria-label={`Link to ${title}`}
        >
          #
        </button>
      </div>
      <div className="space-y-5 text-[15px] leading-7 text-muted-foreground">{children}</div>
    </section>
  );
}

function Subheading({ id, children }: { id?: string; children: ReactNode }) {
  return <h3 id={id} className="scroll-mt-24 pt-4 text-lg font-semibold tracking-tight text-foreground">{children}</h3>;
}

function Callout({
  title,
  children,
  tone = "info",
}: {
  title: string;
  children: ReactNode;
  tone?: "info" | "success" | "warning";
}) {
  const styles = {
    info: "border-border bg-muted/40 text-foreground",
    success: "border-emerald-300/60 bg-emerald-50/70 text-emerald-950 dark:border-emerald-500/25 dark:bg-emerald-500/[0.07] dark:text-emerald-100",
    warning: "border-amber-300/60 bg-amber-50/70 text-amber-950 dark:border-amber-500/25 dark:bg-amber-500/[0.07] dark:text-amber-100",
  };
  const Icon = tone === "success" ? CheckCircle2 : tone === "warning" ? ShieldCheck : Info;
  return (
    <div className={cn("rounded-lg border p-4", styles[tone])}>
      <div className="flex items-start gap-3">
        <Icon className="mt-1 h-4 w-4 shrink-0" />
        <div>
          <p className="mb-1 font-semibold">{title}</p>
          <div className="text-sm leading-6">{children}</div>
        </div>
      </div>
    </div>
  );
}

function StepCards({
  items,
}: {
  items: Array<{ title: string; description: string }>;
}) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {items.map((item, index) => (
        <article key={item.title} className="rounded-lg border border-border bg-card p-5">
          <span className="mb-4 inline-flex h-7 w-7 items-center justify-center rounded-md bg-muted text-xs font-bold text-primary">
            {index + 1}
          </span>
          <h3 className="mb-1 font-semibold text-foreground">{item.title}</h3>
          <p className="text-sm leading-6 text-muted-foreground">{item.description}</p>
        </article>
      ))}
    </div>
  );
}

function RoleQuickstart({ selectedRole, onSelect }: { selectedRole: GuideRole; onSelect: (role: GuideRole) => void }) {
  const guide = ROLE_GUIDES[selectedRole];
  return (
    <div className="overflow-hidden rounded-xl border border-border bg-card">
      <div className="border-b border-border bg-muted/40 p-2">
        <div className="grid grid-cols-2 gap-1 sm:grid-cols-4" role="group" aria-label="Choose guide role">
          {(Object.keys(ROLE_GUIDES) as GuideRole[]).map((role) => (
            <button
              key={role}
              type="button"
              aria-pressed={selectedRole === role}
              onClick={() => onSelect(role)}
              className={cn(
                "rounded-md px-3 py-2 text-sm font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-primary/25",
                selectedRole === role ? "bg-card text-foreground shadow-elevation-xs" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {ROLE_GUIDES[role].label}
            </button>
          ))}
        </div>
      </div>
      <div className="grid gap-6 p-5 lg:grid-cols-[1fr_260px] lg:p-6">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">Your mission</p>
          <h3 className="mt-2 text-xl font-semibold text-foreground">{guide.mission}</h3>
          <ol className="mt-5 grid gap-3 sm:grid-cols-2">
            {guide.first.map((step, index) => (
              <li key={step} className="flex gap-3 text-sm leading-6 text-muted-foreground">
                <span className="mt-0.5 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold text-foreground">{index + 1}</span>
                {step}
              </li>
            ))}
          </ol>
        </div>
        <div className="rounded-lg border border-border bg-muted/40 p-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Authority boundary</p>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">{guide.boundaries}</p>
          <div className="mt-4 flex flex-col gap-2">
            {guide.pages.map((page) => (
              <a key={page.href} href={page.href} className="inline-flex items-center justify-between rounded-md bg-card px-3 py-2 text-xs font-semibold text-foreground shadow-elevation-xs ring-1 ring-border hover:bg-muted">
                {page.label}<ArrowRight className="h-3.5 w-3.5" />
              </a>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function EvidenceLifecycle() {
  const steps = [
    ["Connect", "GitHub App, token or ZIP"],
    ["Scan", "Extract technical facts"],
    ["Review", "Validate topology and gaps"],
    ["Generate", "Create governed project docs"],
    ["Publish", "Push approved output to RAG"],
    ["Verify", "Ask cited questions in Chat"],
  ];
  return (
    <div className="overflow-x-auto rounded-xl border border-border bg-muted/40 p-4">
      <ol className="flex min-w-[820px] items-stretch">
        {steps.map(([title, description], index) => (
          <li key={title} className="flex flex-1 items-center">
            <div className="h-full flex-1 rounded-lg border border-border bg-card p-3">
              <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Stage {index + 1}</span>
              <p className="mt-1 text-sm font-semibold text-foreground">{title}</p>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">{description}</p>
            </div>
            {index < steps.length - 1 && <ArrowRight className="mx-2 h-4 w-4 shrink-0 text-muted-foreground/60" />}
          </li>
        ))}
      </ol>
    </div>
  );
}

type TechnicalView = "ingestion" | "retrieval" | "trust";

const TECHNICAL_VIEWS: Record<TechnicalView, { label: string; description: string }> = {
  ingestion: { label: "Ingestion", description: "How files and generated docs become searchable, scoped chunks." },
  retrieval: { label: "Retrieval", description: "How a question becomes ranked context and inspectable citations." },
  trust: { label: "Trust & debugging", description: "Why the pipeline fails safely and where to look when quality drops." },
};

function TechnicalDeepDive() {
  const [view, setView] = useState<TechnicalView>("ingestion");

  return (
    <div className="overflow-hidden rounded-xl border border-border bg-card">
      <div className="border-b border-border bg-muted/40 p-2">
        <div className="grid gap-1 sm:grid-cols-3" role="tablist" aria-label="Developer architecture views">
          {(Object.keys(TECHNICAL_VIEWS) as TechnicalView[]).map((item) => (
            <button
              key={item}
              type="button"
              role="tab"
              aria-selected={view === item}
              onClick={() => setView(item)}
              className={cn(
                "rounded-md px-4 py-2.5 text-left transition-colors focus:outline-none focus:ring-2 focus:ring-primary/25",
                view === item ? "bg-card text-foreground shadow-elevation-xs" : "text-muted-foreground hover:text-foreground",
              )}
            >
              <span className="block text-sm font-semibold">{TECHNICAL_VIEWS[item].label}</span>
              <span className="mt-0.5 hidden text-xs font-normal text-muted-foreground lg:block">{TECHNICAL_VIEWS[item].description}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="p-5 sm:p-6">
        {view === "ingestion" && <IngestionArchitecture />}
        {view === "retrieval" && <RetrievalArchitecture />}
        {view === "trust" && <TrustArchitecture />}
      </div>
    </div>
  );
}

function TechnicalPipeline({ stages }: { stages: Array<{ icon: typeof Database; title: string; detail: string }> }) {
  return (
    <ol className="grid gap-2 lg:grid-cols-6">
      {stages.map(({ icon: Icon, title, detail }, index) => (
        <li key={title} className="relative rounded-lg border border-border bg-muted/40 p-3">
          <div className="flex items-center justify-between">
            <Icon className="h-4 w-4 text-muted-foreground" />
            <span className="text-[10px] font-semibold text-muted-foreground">0{index + 1}</span>
          </div>
          <p className="mt-3 text-xs font-semibold text-foreground">{title}</p>
          <p className="mt-1 text-[11px] leading-5 text-muted-foreground">{detail}</p>
        </li>
      ))}
    </ol>
  );
}

function TechnicalFact({ title, children }: { title: string; children: ReactNode }) {
  return <article className="border-l-2 border-border pl-4"><h4 className="text-sm font-semibold text-foreground">{title}</h4><div className="mt-1 text-sm leading-6 text-muted-foreground">{children}</div></article>;
}

function IngestionArchitecture() {
  return (
    <div className="space-y-6">
      <div>
        <p className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">Write path</p>
        <h3 className="mt-2 text-xl font-semibold text-foreground">Parse once, preserve structure, store two retrieval signals</h3>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">User uploads and generated Markdown enter the same core pipeline. Every document is scoped to a project before vectorization, and replacing the same project filename removes the old SQL record and Qdrant points first.</p>
      </div>
      <TechnicalPipeline stages={[
        { icon: LockKeyhole, title: "Scope", detail: "project_id, role, groups and revision" },
        { icon: FileCode2, title: "Parse", detail: "PDF, Office or raw Markdown" },
        { icon: Layers3, title: "Chunk", detail: "headings, paragraphs and overlap" },
        { icon: Sparkles, title: "Embed", detail: "dense plus optional sparse" },
        { icon: Database, title: "Upsert", detail: "vectors and protected payload" },
        { icon: CheckCircle2, title: "Commit", detail: "chunk count and job state" },
      ]} />
      <div className="grid gap-5 lg:grid-cols-2">
        <div className="space-y-5">
          <TechnicalFact title="Format-aware parsing">PDF text is read page by page; DOCX headings are preserved as Markdown; PPTX keeps slide boundaries; XLSX emits sheet and row text; Markdown stays unchanged.</TechnicalFact>
          <TechnicalFact title="Structure-aware chunking">Markdown chunks carry a heading path plus <code>section</code> and <code>unit</code>. Other documents merge semantic paragraphs; oversized blocks fall back to token chunks with overlap. XLSX uses mechanical chunks because table rows need continuity.</TechnicalFact>
          <TechnicalFact title="Hybrid representation">Dense embeddings capture semantic similarity. The optional sparse vector hashes normalized terms into a stable 30,000-dimensional space, preserving exact technical names that semantic search can miss.</TechnicalFact>
        </div>
        <div className="rounded-lg border border-border bg-slate-950 p-5 text-slate-200">
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Qdrant point payload</p>
          <pre className="mt-4 overflow-x-auto text-xs leading-6"><code>{`{
  document_id, filename, chunk_index, text,
  allowed_groups: [project_id],
  project_id, document_role,
  source_layer: "canonical_docs",
  section, unit,
  knowledge_revision_id
}`}</code></pre>
          <p className="mt-4 border-t border-slate-700 pt-4 text-xs leading-5 text-slate-400">Base scope fields overwrite caller metadata, so an ingestion request cannot replace protected identifiers such as <code>allowed_groups</code> or <code>document_id</code>.</p>
        </div>
      </div>
    </div>
  );
}

function RetrievalArchitecture() {
  return (
    <div className="space-y-6">
      <div>
        <p className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">Read path</p>
        <h3 className="mt-2 text-xl font-semibold text-foreground">Filter before ranking, then turn chunks into evidence</h3>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">A good answer is not produced by sending the whole knowledge base to a model. Nexus narrows the corpus by authorization and active revision, retrieves candidates using complementary signals, reranks them, restores nearby context and only then constructs the answer prompt.</p>
      </div>
      <TechnicalPipeline stages={[
        { icon: LockKeyhole, title: "Filter", detail: "project, group, role, layer, revision" },
        { icon: Search, title: "Expand", detail: "original plus intent-preserving variants" },
        { icon: ScanSearch, title: "Retrieve", detail: "dense + sparse candidates via RRF" },
        { icon: Layers3, title: "Rerank", detail: "cross-encoder relevance gate" },
        { icon: GitBranch, title: "Expand context", detail: "neighbors or whole section" },
        { icon: Sparkles, title: "Answer", detail: "source-first prompt and citations" },
      ]} />
      <div className="grid gap-5 md:grid-cols-2">
        <TechnicalFact title="Authorization is part of the vector query">Qdrant filters combine assigned projects, allowed groups, document role, corpus layer and the active knowledge revision. Prepared but inactive revisions are not eligible for normal answers.</TechnicalFact>
        <TechnicalFact title="Dense and sparse are fused">Dense search finds conceptual matches; sparse search protects identifiers and exact terms. Qdrant prefetches both candidate sets and applies Reciprocal Rank Fusion before the final top set.</TechnicalFact>
        <TechnicalFact title="Reranking is a quality gate">The original question and candidate texts are scored together by the reranker. Low-scoring results are dropped. If the external reranker fails, Nexus falls back to the original retrieval order instead of failing the whole request.</TechnicalFact>
        <TechnicalFact title="Source evidence stays primary">Source-role documents are retrieved independently from bounded reference, guidance, policy, standard and template context. The latter can shape interpretation but should not silently become factual proof.</TechnicalFact>
        <TechnicalFact title="Context is reconstructed">High-scoring chunks can pull adjacent chunks for continuity. Deeper retrieval can also expand a matched unit or section, which avoids answering from an isolated sentence.</TechnicalFact>
        <TechnicalFact title="Citations come from persisted payloads">Chunks are merged per document, ordered by chunk index and assigned stable response reference numbers. The answer and returned source objects are persisted with latency and usage evidence.</TechnicalFact>
      </div>
    </div>
  );
}

function TrustArchitecture() {
  const diagnostics = [
    ["Upload succeeds, zero useful chunks", "Inspect parser output, file text extraction and chunk count. Image-only PDFs need extractable text before this pipeline can ground answers."],
    ["Chunks exist, but Chat finds nothing", "Check the selected project, allowed groups, document role, source layer, active revision and relevance threshold before changing the prompt."],
    ["Exact component names are missed", "Confirm hybrid search is enabled and that the identifier exists in chunk text or unit metadata; sparse retrieval is designed for this case."],
    ["Answer is relevant but incomplete", "Inspect neighboring chunks and section boundaries. A heading or table split can hide required context even when the first hit is correct."],
    ["Project Docs changed, answer did not", "Generating a file does not publish it. Push the reviewed docs to RAG and verify the active revision before retesting."],
    ["Old content keeps appearing", "Confirm the logical project filename was replaced and the intended knowledge revision is active; citations expose filename, section and revision metadata."],
  ];
  return (
    <div className="space-y-6">
      <div>
        <p className="text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">Operational confidence</p>
        <h3 className="mt-2 text-xl font-semibold text-foreground">Every weak answer can be traced to a pipeline stage</h3>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">Nexus keeps ingestion jobs, chunk counts, vector payloads, revisions, query sources, latency and usage as separate evidence. That makes “the AI was wrong” a diagnosable claim rather than a black box.</p>
      </div>
      <div className="grid gap-3 lg:grid-cols-2">
        {diagnostics.map(([symptom, check]) => <article key={symptom} className="rounded-lg border border-border bg-muted/40 p-4"><div className="flex items-start gap-3"><Clock3 className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" /><div><h4 className="text-sm font-semibold text-foreground">{symptom}</h4><p className="mt-1 text-sm leading-6 text-muted-foreground">{check}</p></div></div></article>)}
      </div>
      <Callout title="The model is the final stage, not the knowledge base" tone="success">The strongest quality controls happen before generation: source classification, project scoping, active revisions, hybrid retrieval, reranking and context reconstruction. Citations then let the user audit what actually reached the model.</Callout>
    </div>
  );
}

function ScreenshotCard({
  src,
  alt,
  caption,
}: {
  src: string;
  alt: string;
  caption: string;
}) {
  return (
    <figure className="overflow-hidden rounded-lg border border-border bg-card">
      <div className="relative flex h-[360px] items-center justify-center bg-muted/40 sm:h-[440px]">
        <Image
          src={src}
          alt={alt}
          fill
          sizes="(max-width: 768px) 100vw, 900px"
          className="object-contain p-6"
        />
      </div>
      <figcaption className="flex flex-col gap-3 border-t border-border px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <span className="text-xs text-muted-foreground">{caption}</span>
        <a href={src} target="_blank" rel="noreferrer" className={pageLinkClass}>
          Open full size <ExternalLink className="h-3.5 w-3.5" />
        </a>
      </figcaption>
    </figure>
  );
}

function Table({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-card">
      <table className="w-full min-w-[720px] border-collapse text-left text-sm">{children}</table>
    </div>
  );
}

function TH({ children }: { children: ReactNode }) {
  return <th scope="col" className="border-b border-border bg-muted/40 px-4 py-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{children}</th>;
}

function TD({ children }: { children: ReactNode }) {
  return <td className="border-b border-border/60 px-4 py-3 align-top text-muted-foreground last:border-b-0">{children}</td>;
}

function PageReference({
  title,
  route,
  purpose,
  access,
  actions,
  prerequisites,
  result,
}: {
  title: string;
  route: string;
  purpose: string;
  access: string;
  actions: string[];
  prerequisites?: string;
  result?: string;
}) {
  return (
    <article className="overflow-hidden rounded-lg border border-border bg-card">
      <div className="flex flex-wrap items-center gap-3 border-b border-border bg-muted/40 px-5 py-4">
        <h3 className="font-semibold text-foreground">{title}</h3>
        <code className="rounded border border-border bg-card px-2 py-0.5 text-xs text-foreground">{route}</code>
        <a href={route} className={cn(pageLinkClass, "ml-auto")}>
          Open page <ArrowRight className="h-3.5 w-3.5" />
        </a>
      </div>
      <dl className="grid sm:grid-cols-[150px_1fr]">
        <dt className="border-b border-border/60 px-5 py-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Purpose</dt>
        <dd className="border-b border-border/60 px-5 py-3">{purpose}</dd>
        <dt className="border-b border-border/60 px-5 py-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Access</dt>
        <dd className="border-b border-border/60 px-5 py-3">{access}</dd>
        {prerequisites && <><dt className="border-b border-border/60 px-5 py-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Before you begin</dt><dd className="border-b border-border/60 px-5 py-3">{prerequisites}</dd></>}
        <dt className={cn("px-5 py-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground", result && "border-b border-border/60")}>Main actions</dt>
        <dd className={cn("px-5 py-3", result && "border-b border-border/60")}>
          <ul className="list-disc space-y-1 pl-5">{actions.map((action) => <li key={action}>{action}</li>)}</ul>
        </dd>
        {result && <><dt className="px-5 py-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Expected result</dt><dd className="px-5 py-3">{result}</dd></>}
      </dl>
    </article>
  );
}

function DocsNav({
  groups,
  activeId,
  onNavigate,
}: {
  groups: NavGroup[];
  activeId: string;
  onNavigate?: () => void;
}) {
  return (
    <nav aria-label="Documentation navigation" className="space-y-6">
      {groups.map((group) => (
        <div key={group.label}>
          <p className="mb-2 px-3 text-[10px] font-bold uppercase tracking-[0.14em] text-muted-foreground">{group.label}</p>
          <div className="space-y-0.5">
            {group.items.map((item) => (
              <a
                key={item.id}
                href={`#${item.id}`}
                aria-current={activeId === item.id ? "location" : undefined}
                onClick={(event) => {
                  event.preventDefault();
                  scrollToSection(item.id);
                  onNavigate?.();
                }}
                className={cn(
                  "block w-full rounded-md px-3 py-2 text-left text-sm transition-colors",
                  activeId === item.id
                    ? "bg-muted font-medium text-foreground"
                    : "text-muted-foreground hover:bg-muted/50 hover:text-foreground",
                )}
              >
                {item.label}
              </a>
            ))}
          </div>
        </div>
      ))}
    </nav>
  );
}

/**
 * Switch between the two editions.
 *
 * Only rendered for a reader who actually has administrative access. Offering a
 * member a "Running the workspace" edition would just be a door into chapters
 * about controls they cannot open; and an admin genuinely needs the member
 * edition, to see what their own users are reading.
 */
function EditionSwitch({
  edition,
  onChange,
}: {
  edition: Edition;
  onChange: (next: Edition) => void;
}) {
  return (
    <div className="rounded-xl border border-border bg-card p-1.5">
      <div
        className="grid gap-1 sm:grid-cols-2"
        role="group"
        aria-label="Choose guide edition"
      >
        {(Object.keys(EDITIONS) as Edition[]).map((candidate) => {
          const active = candidate === edition;
          return (
            <button
              key={candidate}
              type="button"
              aria-pressed={active}
              // Explicit name: without it the accessible name is the label plus
              // the blurb, and the "running" blurb contains the words "Using
              // the app" — so both buttons announce nearly the same thing.
              aria-label={EDITIONS[candidate].label}
              onClick={() => onChange(candidate)}
              className={cn(
                "rounded-lg px-4 py-3 text-left transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
                active
                  ? "bg-primary/[0.08] ring-1 ring-primary/30"
                  : "hover:bg-muted/50",
              )}
            >
              <span
                className={cn(
                  "block text-sm font-semibold",
                  active ? "text-foreground" : "text-muted-foreground",
                )}
              >
                {EDITIONS[candidate].label}
              </span>
              <span className="mt-0.5 block text-xs leading-5 text-muted-foreground">
                {EDITIONS[candidate].blurb}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function ProductGuide() {
  const [activeId, setActiveId] = useState("overview");
  const [query, setQuery] = useState("");
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [selectedRole, setSelectedRole] = useState<GuideRole>("developer");
  const searchRef = useRef<HTMLInputElement>(null);

  const router = useRouter();
  const { status, startTour } = useOnboardingContext();
  const { hasPermission, hasAnyAdminPermission } = usePermissions();
  const persona = status?.persona;

  /**
   * Whether this reader has anything to read in the "running" edition at all.
   * Derived from permissions rather than persona so it matches what the
   * chapters themselves are gated on.
   */
  const canRunWorkspace =
    hasAnyAdminPermission ||
    ["manage_users", "manage_projects", "view_audit_logs"].some((codename) =>
      hasPermission(codename as Parameters<typeof hasPermission>[0]),
    );

  const [edition, setEdition] = useState<Edition>("using");
  /** True once the reader picks an edition, so their choice is not overwritten. */
  const editionChosen = useRef(false);

  // Open on the edition that matches the reader. An admin lands on "running"
  // because that is the part they cannot get anywhere else; a member never sees
  // the switch at all.
  useEffect(() => {
    if (editionChosen.current) return;
    const requested = new URLSearchParams(window.location.search).get("edition");
    if (requested === "using" || requested === "running") {
      setEdition(requested === "running" && !canRunWorkspace ? "using" : requested);
      return;
    }
    setEdition(canRunWorkspace ? "running" : "using");
  }, [canRunWorkspace]);

  function chooseEdition(next: Edition) {
    editionChosen.current = true;
    setEdition(next);
    const url = new URL(window.location.href);
    url.searchParams.set("edition", next);
    window.history.replaceState(null, "", `${url.pathname}${url.search}`);
  }

  const groups = useMemo(
    () =>
      visibleGroups(edition, (codename) =>
        hasPermission(codename as Parameters<typeof hasPermission>[0]),
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [edition, persona],
  );

  const visibleItems = useMemo(
    () => groups.flatMap((group) => group.items),
    [groups],
  );

  /** Ids the current edition renders — gates both search and the section list. */
  const visibleIds = useMemo(
    () => new Set(visibleItems.map((item) => item.id)),
    [visibleItems],
  );

  /** Should a `runningOnly` section render for this reader right now? */
  const showsRunning = (id: string) => visibleIds.has(id);

  // Offer the walkthrough that matches the reader, with the real step count.
  const walkthrough = useMemo(() => {
    const definition = tourFor(persona ?? "developer");
    const steps = applicableSteps(definition.steps, {
      hasPermission: (codename) =>
        hasPermission(codename as Parameters<typeof hasPermission>[0]),
    });
    return { definition, count: steps.length };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [persona]);

  // Search only what is rendered. Returning a hidden chapter would scroll to an
  // element that does not exist, which reads as a broken search box.
  const searchResults = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (normalized.length < 2) return [];
    return visibleItems
      .filter((item) =>
        `${item.label} ${item.keywords ?? ""} ${item.summary}`
          .toLowerCase()
          .includes(normalized),
      )
      .slice(0, 8);
  }, [query, visibleItems]);

  // Default to the reader's real authority, so the guide opens on the section
  // that applies to them. An explicit ?role= still wins — the point of that
  // parameter is sharing a specific view with someone else.
  useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get(
      "role",
    ) as GuideRole | null;
    if (requested && ROLE_GUIDES[requested]) {
      setSelectedRole(requested);
      return;
    }
    if (persona) setSelectedRole(GUIDE_ROLE_FOR_PERSONA[persona]);
  }, [persona]);

  // A link to a chapter the reader's edition hides must still work: switch the
  // edition rather than silently doing nothing, otherwise a shared
  // `#admin-pages` link is a dead link for the person who received it.
  useEffect(() => {
    const updateFromHash = () => {
      const id = window.location.hash.slice(1);
      if (!id) return;
      if (visibleIds.has(id)) {
        setActiveId(id);
        return;
      }
      if (RUNNING_ONLY_SECTIONS.has(id) && canRunWorkspace) {
        editionChosen.current = true;
        setEdition("running");
        setActiveId(id);
      }
    };
    updateFromHash();
    window.addEventListener("hashchange", updateFromHash);
    return () => window.removeEventListener("hashchange", updateFromHash);
  }, [visibleIds, canRunWorkspace]);

  // Re-observe when the edition changes: the set of rendered sections changed,
  // so an observer bound to the old set would leave the nav highlight stuck.
  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries.find((entry) => entry.isIntersecting);
        if (visible?.target.id) setActiveId(visible.target.id);
      },
      { rootMargin: "-90px 0px -72% 0px", threshold: 0.01 },
    );
    visibleItems.forEach((item) => {
      const section = document.getElementById(item.id);
      if (section) observer.observe(section);
    });
    return () => observer.disconnect();
  }, [visibleItems]);

  useEffect(() => {
    function handleShortcut(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const editing = target?.tagName === "INPUT" || target?.tagName === "TEXTAREA";
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchRef.current?.focus();
      } else if (event.key === "/" && !editing) {
        event.preventDefault();
        searchRef.current?.focus();
      } else if (event.key === "Escape") {
        setQuery("");
        setMobileNavOpen(false);
        searchRef.current?.blur();
      }
    }
    window.addEventListener("keydown", handleShortcut);
    return () => window.removeEventListener("keydown", handleShortcut);
  }, []);

  function chooseSearchResult(id: string) {
    setQuery("");
    scrollToSection(id);
  }

  function chooseRole(role: GuideRole) {
    setSelectedRole(role);
    const url = new URL(window.location.href);
    url.searchParams.set("role", role);
    window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  }

  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="fixed inset-x-0 top-0 z-50 h-14 border-b border-border bg-background/95 backdrop-blur">
        <div className="flex h-full items-center gap-3 px-4 lg:px-5">
          <button
            type="button"
            onClick={() => setMobileNavOpen(true)}
            className="inline-flex h-9 w-9 items-center justify-center rounded-md border border-border text-muted-foreground md:hidden"
            aria-label="Open documentation navigation"
          >
            <Menu className="h-4 w-4" />
          </button>
          <a href="/guide" className="flex w-auto shrink-0 items-center gap-2 text-sm font-semibold text-foreground md:w-[220px]">
            <Image src="/guide/screenshots/intodata-logo.png" alt="" width={22} height={22} className="rounded" />
            Nexus <span className="font-medium text-muted-foreground">Docs</span>
          </a>

          <div className="relative mx-auto hidden w-full max-w-xl sm:block">
            <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
            <input
              ref={searchRef}
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search product documentation…"
              className="h-9 w-full rounded-md border border-border bg-card pl-9 pr-12 text-sm text-foreground shadow-elevation-xs outline-none placeholder:text-muted-foreground focus:border-ring focus:ring-2 focus:ring-primary/10"
              aria-label="Search product documentation"
            />
            <kbd className="pointer-events-none absolute right-2.5 top-2 rounded border border-border bg-muted/40 px-1.5 py-0.5 text-[10px] text-muted-foreground">⌘K</kbd>
            {query.trim().length >= 2 && (
              <div className="absolute inset-x-0 top-11 overflow-hidden rounded-lg border border-border bg-card shadow-elevation-lg">
                {searchResults.length ? (
                  searchResults.map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      aria-label={`Open ${item.label}`}
                      onClick={() => chooseSearchResult(item.id)}
                      className="flex w-full items-center justify-between border-b border-border/60 px-4 py-3 text-left text-sm text-foreground last:border-0 hover:bg-muted/50"
                    >
                      <span>
                        <span className="block font-medium text-foreground">{item.label}</span>
                        <span className="mt-0.5 block text-xs text-muted-foreground">{item.summary}</span>
                      </span>
                      <span className="ml-4 text-xs text-muted-foreground">Open</span>
                    </button>
                  ))
                ) : (
                  <p className="px-4 py-3 text-sm text-muted-foreground">No matching section</p>
                )}
              </div>
            )}
          </div>

          <div className="ml-auto flex shrink-0 items-center gap-3">
            <span className="hidden text-xs text-muted-foreground lg:inline">Last verified {LAST_VERIFIED_SHORT}</span>
            <a href="/" className={pageLinkClass}>
              <ArrowLeft className="h-3.5 w-3.5" /> Back to Nexus
            </a>
          </div>
        </div>
      </header>

      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 overflow-y-auto border-r border-border bg-card px-3 pb-8 pt-20 md:block">
        <div className="mb-6 border-b border-border px-3 pb-5">
          <p className="text-sm font-semibold text-foreground">Nexus documentation</p>
          <p className="mt-1 text-xs text-muted-foreground">Last verified {LAST_VERIFIED_SHORT}</p>
        </div>
        <DocsNav groups={groups} activeId={activeId} />
      </aside>

      {mobileNavOpen && (
        <div className="fixed inset-0 z-[60] md:hidden">
          <button
            type="button"
            className="absolute inset-0 bg-foreground/20"
            onClick={() => setMobileNavOpen(false)}
            aria-label="Close documentation navigation"
          />
          <aside className="relative h-full w-[min(86vw,320px)] overflow-y-auto border-r border-border bg-card p-4 shadow-xl">
            <div className="mb-6 flex items-center justify-between border-b border-border pb-4">
              <div className="flex items-center gap-2 text-sm font-semibold">
                <BookOpen className="h-4 w-4" /> Contents
              </div>
              <button type="button" onClick={() => setMobileNavOpen(false)} className="rounded-md p-2 text-muted-foreground hover:bg-muted" aria-label="Close navigation">
                <X className="h-4 w-4" />
              </button>
            </div>
            <DocsNav groups={groups} activeId={activeId} onNavigate={() => setMobileNavOpen(false)} />
          </aside>
        </div>
      )}

      <aside className="fixed inset-y-0 right-0 hidden w-60 overflow-y-auto border-l border-border/60 bg-card px-8 pb-8 pt-24 xl:block">
        <p className="mb-3 text-xs font-semibold text-foreground">On this page</p>
        <nav className="border-l border-border" aria-label="On this page">
          <a href={`#${activeId}`} className="-ml-px block border-l border-primary px-3 py-1.5 text-xs font-medium text-primary">
            {visibleItems.find((item) => item.id === activeId)?.label}
          </a>
          {(TOPIC_SUBHEADINGS[activeId] ?? []).map((item) => (
            <a key={item.id} href={`#${item.id}`} className="block border-l border-transparent px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground">
              {item.label}
            </a>
          ))}
        </nav>
        <div className="mt-8 border-t border-border pt-5">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Reading</p>
          <p className="mt-1 text-sm font-medium text-foreground">{EDITIONS[edition].label}</p>
          {canRunWorkspace && (
            <button
              type="button"
              onClick={() =>
                chooseEdition(edition === "running" ? "using" : "running")
              }
              className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
            >
              Switch to {EDITIONS[edition === "running" ? "using" : "running"].label}
              <ArrowRight className="h-3 w-3" aria-hidden />
            </button>
          )}
          <p className="mt-4 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Viewing as</p>
          <p className="mt-1 text-sm font-medium text-foreground">{ROLE_GUIDES[selectedRole].label}</p>
          <a href="#quickstarts" className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline">Change role <ArrowRight className="h-3 w-3" /></a>
        </div>
      </aside>

      <div className="pt-14 md:pl-64 xl:pr-60">
        <main className="mx-auto max-w-5xl px-5 pb-20 sm:px-8 lg:px-12">
          <div className="border-b border-border py-12 sm:py-16">
            <p className="mb-4 text-xs font-medium text-muted-foreground">Nexus / Documentation / Product guide</p>
            <h1 className="max-w-3xl text-4xl font-semibold tracking-[-0.035em] text-foreground sm:text-5xl">
              {edition === "running"
                ? "Running the workspace"
                : "Using the app"}
            </h1>
            <p className="mt-5 max-w-3xl text-lg leading-8 text-muted-foreground">
              {edition === "running"
                ? "How to operate this deployment: who gets access and with what authority, how evidence gets in and published, and how to see what the platform is doing. Everything in the member guide is here too."
                : "How to sign in, ask questions that cite their sources, read generated documentation, navigate the topology, and look after your own account."}
            </p>
            <div className="mt-7 flex flex-wrap gap-x-5 gap-y-2 text-xs text-muted-foreground">
              <span>
                {edition === "running"
                  ? "Owner · Admin · Project owner"
                  : "Everyone with an account"}
              </span>
              <span>Last verified {LAST_VERIFIED}</span>
              <span>{visibleItems.length} chapters</span>
            </div>
          </div>

          <Section id="overview" title="Overview">
            <p className="text-lg leading-8 text-foreground">
              Nexus turns project evidence into a searchable knowledge base, generated documentation, interactive topology, and operational insight. What you can see and change depends on your platform role and project assignments.
            </p>

            {/* Only for a reader who has administrative access. Showing a member
                a door into chapters about controls they cannot open would be a
                dead end, so they simply get the member edition. */}
            {canRunWorkspace && (
              <EditionSwitch edition={edition} onChange={chooseEdition} />
            )}
            {/* Reading and doing are different modes. The guide is the reference;
                the walkthrough highlights the same controls in the live app.
                Previously there was no way to reach the tour again once it had
                been finished or skipped. */}
            <div className="rounded-xl border border-primary/30 bg-primary/[0.05] p-5">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-foreground">
                    Prefer to be shown rather than read?
                  </p>
                  <p className="mt-1 max-w-xl text-sm leading-6 text-muted-foreground">
                    {walkthrough.definition.pitch} It highlights the real
                    controls in the app and you can stop at any point.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    // The tour anchors to app chrome that /guide does not
                    // render, so it has to run on a product page. Client
                    // navigation, not a location assignment: a full reload
                    // would discard the state startTour() just set.
                    startTour();
                    router.push("/");
                  }}
                  className="inline-flex h-9 shrink-0 items-center gap-2 rounded-md bg-primary px-4 text-xs font-semibold text-primary-foreground shadow-elevation-xs transition-colors hover:bg-primary/90 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
                >
                  {`Start the ${walkthrough.count}-step walkthrough`}
                  <ArrowRight className="h-3.5 w-3.5" aria-hidden />
                </button>
              </div>
            </div>

            <Callout title="How to use this guide">
              Choose your role in Quickstarts, follow a complete workflow, and use the page references when you need exact controls, prerequisites, expected results or recovery steps.
            </Callout>
            <Subheading>From source to trusted answer</Subheading>
            <EvidenceLifecycle />
            <StepCards items={[
              { title: "Get access", description: "Request access or use an invitation, sign in, enrol an authenticator, and create a first password when required." },
              { title: "Choose a project", description: "Project selectors only show the scope your role and assignments allow." },
              { title: "Work from evidence", description: "Upload sources, ask cited questions, inspect generated docs, and follow topology back to technical evidence." },
              { title: "Operate by authority", description: "Admins handle daily access and project work; the Owner retains role-definition and global security authority." },
            ]} />
          </Section>

          <Section id="architecture-map" title="Architecture map">
            <p>
              Start zoomed out to see how the four blocks relate, then zoom into
              a subsystem for the mechanism behind it. Every level is the same
              underlying model, so nothing here can drift out of step with a
              hand-drawn diagram.
            </p>
            <ArchitectureExplorer />
          </Section>

          <Section id="quickstarts" title="Quickstarts by role">
            <p>Select the responsibility you are working under. The choice is stored in the URL as <code className="rounded bg-muted px-1.5 py-0.5 text-xs text-foreground">?role=</code>, so you can share the same view with a teammate.</p>
            <RoleQuickstart selectedRole={selectedRole} onSelect={chooseRole} />
            <Callout title="Roles and project ownership are separate">
              Developer, Admin and Owner describe organization authority. Project owner is an assignment on one project and can coexist with either Developer or Admin.
            </Callout>
          </Section>

          <Section id="access" title="Access and sign-in">
            <p>Nexus supports invitation/setup onboarding, email-code sign-in, and password sign-in. MFA is required before the rest of the application becomes available.</p>
            <Subheading id="account-invite">Invitation path</Subheading>
            <ol className="list-decimal space-y-2 pl-5">
              <li>Request access from the login page or open the invitation supplied by your organization.</li>
              <li>An Owner or Admin approves the request and chooses the role the invitation carries — including whether it grants administration. They can also pre-assign projects and add a welcome note.</li>
              <li>Use the setup link or email code within its validity window: invitation links last 48 hours and email codes last 10 minutes.</li>
              <li>Complete your profile. The profile field “Role or title” is descriptive metadata; it does not grant RBAC permissions.</li>
              <li>The account is created with the role recorded on the invitation, then continues to mandatory MFA and first-password setup. Invitations sent without an explicit role fall back to the default member role.</li>
            </ol>
            <Subheading id="email-login">Sign in with an email code</Subheading>
            <div className="grid gap-2 sm:grid-cols-5">
              {["Enter email", "Email code", "Authenticator", "Create password", "Open Nexus"].map((step, index) => (
                <div key={step} className="rounded-md border border-border bg-card px-3 py-3 text-center text-xs font-semibold text-foreground">
                  <span className="mr-1 text-muted-foreground">{index + 1}.</span> {step}
                </div>
              ))}
            </div>
            <Subheading id="password-login">Sign in with a password</Subheading>
            <ol className="list-decimal space-y-2 pl-5">
              <li>Select <strong className="text-foreground">Use password instead</strong>.</li>
              <li>Enter your username or email and password.</li>
              <li>Complete MFA with an authenticator or unused backup code.</li>
            </ol>
            <Callout title="No password exists yet?" tone="warning">
              Nexus tells you that the account has no password and switches you to email-code login. After email and MFA verification, create the first password.
            </Callout>
            <Callout title="Login protection">
              Email codes allow five verification attempts. Five failed password sign-ins lock the account for 15 minutes; wait for the lock to expire instead of repeatedly retrying.
            </Callout>
          </Section>

          <Section id="first-password" title="Create or change a password">
            <Subheading id="password-create">Mandatory first password</Subheading>
            <ol className="list-decimal space-y-2 pl-5">
              <li>Sign in with an email code and complete MFA.</li>
              <li>Enter and confirm a new password of 12–128 characters.</li>
              <li>No current password is requested because none exists.</li>
              <li>After saving, other refresh sessions are revoked while the current session continues.</li>
            </ol>
            <Subheading id="password-change">Change an existing password</Subheading>
            <ol className="list-decimal space-y-2 pl-5">
              <li>Open Profile from the account controls.</li>
              <li>Enter the current password.</li>
              <li>Enter and confirm the replacement password.</li>
              <li>Nexus verifies the current password before saving the change.</li>
              <li>The replacement must differ from the current password and be 12–128 characters.</li>
            </ol>
            <Subheading id="password-reset">Administrator reset</Subheading>
            <p>An Owner or Admin with member-management authority can reset an ordinary member password. This revokes all refresh sessions for that member. Existing short-lived access sessions may continue until they expire, so use account deactivation as well when access must stop immediately.</p>
            <Callout title="Account-safe behavior" tone="success">
              Creating a first password requires an authenticated MFA-enabled session and only works when no password exists. Changing an existing password requires the correct current password. Admins cannot read passwords or reset an Owner account.
            </Callout>
          </Section>

          <Section id="mfa" title="Two-factor authentication">
            <p>MFA uses a time-based one-time password (TOTP). It is separate from the six-digit code delivered by email.</p>
            <Table>
              <thead><tr><TH>Code</TH><TH>Delivered by</TH><TH>Purpose</TH></tr></thead>
              <tbody>
                <tr><TD>Email code</TD><TD>Mailbox</TD><TD>Passwordless first factor and account proof.</TD></tr>
                <tr><TD>Authenticator code</TD><TD>Authenticator app</TD><TD>Required second factor after email or password sign-in.</TD></tr>
                <tr><TD>Backup code</TD><TD>Shown during setup</TD><TD>One-time replacement when the authenticator is unavailable.</TD></tr>
              </tbody>
            </Table>
            <Subheading id="mfa-enrol">Enrol MFA</Subheading>
            <ol className="list-decimal space-y-2 pl-5">
              <li>Open Secure your account and select Set up 2FA.</li>
              <li>Scan the QR code with a TOTP authenticator.</li>
              <li>Verify the six-digit authenticator code.</li>
              <li>Save every backup code in a password manager.</li>
            </ol>
            <Subheading id="mfa-recovery">Recovery limits</Subheading>
            <ul className="list-disc space-y-2 pl-5">
              <li>Nexus generates ten backup codes. Each code is single-use and displayed only once.</li>
              <li>Disabling MFA requires the current password plus an authenticator or unused backup code.</li>
              <li>Backup codes cannot currently be regenerated separately; replacing MFA creates a new recovery set.</li>
              <li>When neither the authenticator nor a backup code is available, use the verified organization recovery process.</li>
            </ul>
          </Section>

          <Section id="concepts" title="Core concepts">
            <div className="grid gap-3 sm:grid-cols-2">
              {[
                ["Organization", "The complete Nexus environment with protected deployment Owner authority, users, roles, projects and shared settings."],
                ["Project", "The main security and evidence boundary for chat, docs, topology, source scans, and runtime evidence."],
                ["Assignment", "A link granting project access. It does not automatically grant project management."],
                ["Project owner", "The single assignment allowed to administer one project; this is not the global Owner role."],
                ["Source", "A repository or archive registered for scanning."],
                ["Evidence document", "An uploaded file whose role identifies whether it proves facts or guides interpretation."],
                ["Project Docs", "Generated Markdown and PDF documentation derived from project evidence and the active Pack."],
                ["Topology", "Evidence-preserving navigation from systems and exchanges to jobs, components, and mappings."],
                ["Documentation Pack", "A governed definition of the purpose, structure, evidence requirements and style of generated output."],
                ["Runtime source", "A configured operational provider used to collect executions, jobs, incidents and health evidence."],
              ].map(([title, description]) => (
                <article key={title} className="rounded-lg border border-border bg-card p-5">
                  <h3 className="mb-1 font-semibold text-foreground">{title}</h3>
                  <p className="text-sm leading-6">{description}</p>
                </article>
              ))}
            </div>
          </Section>

          <Section id="roles" title="Roles and permissions">
            <p>Nexus evaluates organization authority, explicit permissions and project scope separately. “Owner” is the product name for the deployment-wide protected authority.</p>
            <Subheading id="rbac-layers">Three access layers</Subheading>
            <div className="grid gap-3 sm:grid-cols-3">
              {[
                [Users, "Organization role", "Developer or Admin describes reusable platform permissions; Owner bypasses ordinary role checks."],
                [LockKeyhole, "Capability grant", "A role grants classes of actions such as asking questions, ingesting evidence or managing ordinary members."],
                [Route, "Project assignment", "An assignment limits data scope. is_owner adds management authority for that one project."],
              ].map(([Icon, title, description]) => {
                const IconComponent = Icon as typeof Users;
                return <article key={title as string} className="rounded-lg border border-border bg-card p-5"><IconComponent className="h-5 w-5 text-muted-foreground" /><h3 className="mt-4 font-semibold text-foreground">{title as string}</h3><p className="mt-1 text-sm leading-6">{description as string}</p></article>;
              })}
            </div>
            <Subheading id="role-matrix">Default role and assignment matrix</Subheading>
            <Table>
              <thead><tr><TH>Capability</TH><TH>Owner</TH><TH>Admin</TH><TH>Project-owner assignment</TH><TH>Developer</TH></tr></thead>
              <tbody>
                <tr><TD>Global configuration and security</TD><TD>Full</TD><TD>—</TD><TD>—</TD><TD>—</TD></tr>
                <tr><TD>Manage ordinary users and access</TD><TD>Yes</TD><TD>Yes</TD><TD>—</TD><TD>—</TD></tr>
                <tr><TD>Create or change role definitions</TD><TD>Yes</TD><TD>—</TD><TD>—</TD><TD>—</TD></tr>
                <tr><TD>Create projects</TD><TD>Yes</TD><TD>Yes; becomes owner</TD><TD>—</TD><TD>—</TD></tr>
                <tr><TD>Manage projects and sources</TD><TD>All projects</TD><TD>Owned projects</TD><TD>That project</TD><TD>Not in supported UI</TD></tr>
                <tr><TD>Use Chat, Documents, Docs, Topology</TD><TD>All projects</TD><TD>Authorized scope</TD><TD>Owned project</TD><TD>Assigned projects</TD></tr>
              </tbody>
            </Table>
            <Callout title="Protected Owner boundary" tone="warning">
              The intended hierarchy has one deployment Owner, although upgraded installations can contain multiple legacy protected Owner accounts. Ordinary member administration cannot edit, deactivate, reset, or change any Owner account. Owner succession requires a separate protected process.
            </Callout>
          </Section>

          <Section id="developer-architecture" title="Developer architecture: evidence to answer">
            <p className="text-lg leading-8 text-foreground">Nexus is designed as an evidence system around the model—not a chat wrapper. The technical quality comes from preserving structure during ingestion, applying project and revision boundaries inside retrieval, combining semantic and exact-term search, and returning the evidence that actually reached generation.</p>
            <TechnicalDeepDive />
            <Subheading id="developer-ingestion">Three ways knowledge enters Nexus</Subheading>
            <Table>
              <thead><tr><TH>Entry path</TH><TH>What enters</TH><TH>Shared pipeline</TH></tr></thead>
              <tbody>
                <tr><TD>Documents</TD><TD>PDF, DOCX, PPTX, XLSX or Markdown uploaded to one project with an explicit document role.</TD><TD>Parse → structure-aware chunk → dense/sparse embed → Qdrant.</TD></tr>
                <tr><TD>Generated Project Docs</TD><TD>Reviewed Markdown pushed from the source pipeline through the RAG bridge.</TD><TD>Idempotent project filename replacement, then the same chunk/embed/store path.</TD></tr>
                <tr><TD>Internal support layers</TD><TD>Technical analysis, support maps, scorecards and evidence slices produced during analysis.</TD><TD>Stored as separate source layers and only searched by the deeper retrieval path.</TD></tr>
              </tbody>
            </Table>
            <Callout title="Repositories are not blindly embedded" tone="success">
              GitHub and ZIP sources first go through deterministic platform scanning. Nexus extracts jobs, routes, components, mappings, lineage and warnings into structured artifacts. Those facts power Topology and governed document generation; only reviewed generated output is published into the canonical RAG corpus.
            </Callout>
            <TechnicalPipeline stages={[
              { icon: GitBranch, title: "Connect", detail: "managed GitHub or ZIP workspace" },
              { icon: ScanSearch, title: "Scan", detail: "platform-aware intodoc extraction" },
              { icon: Database, title: "Facts", detail: "jobs, routes, lineage and warnings" },
              { icon: Layers3, title: "Generate", detail: "phase-based Pack execution" },
              { icon: ShieldCheck, title: "Review", detail: "quality gates and evidence checks" },
              { icon: Sparkles, title: "Publish", detail: "approved Markdown through RAG bridge" },
            ]} />
            <p>A scan owns its output directory exclusively, clears stale approval artifacts before starting and fails closed when the scanner does not produce a valid result. For Talend, a zero-job scan is treated as failure rather than a misleading success. Document generation runs ordered phases, supports deliberate skips/resume behavior and stops on the first fatal phase failure.</p>
            <Subheading id="developer-retrieval">Why retrieval is stronger than a single vector search</Subheading>
            <p>The standard path searches canonical docs using project and role filters. When enabled, query expansion produces intent-preserving variants; hybrid search combines dense cosine similarity with sparse exact-term matching through Reciprocal Rank Fusion; reranking then scores the candidate text against the original question. Neighbor expansion restores local continuity before source chunks and bounded auxiliary context are passed to generation.</p>
            <Subheading id="developer-debugging">What a citation proves</Subheading>
            <p>A citation identifies the persisted document, chunk indexes, project, document role, source layer and section used to build the prompt. It proves which evidence was supplied to the model; it does not automatically prove that the model interpreted every sentence correctly. That is why the UI keeps source cards inspectable.</p>
          </Section>

          <Section id="chat" title="Chat and Deep Dive">
            <ScreenshotCard src="/guide/screenshots/chat-welcome.png" alt="Current Nexus Chat landing view" caption="Chat landing view with Chat and Deep Dive modes, project scope, runtime status, and attachment controls." />
            <PageReference title="Chat" route="/" purpose="Ask project questions and verify answers against authorized evidence." access="Authenticated users with query access; evidence is restricted to authorized projects." prerequisites="Select the correct project and confirm that sources or documents have completed indexing." actions={["Choose Standard Chat for focused retrieval or Deep Dive for broader analysis.", "Use slash commands and the Pack workbench when the intended workflow offers them.", "Attach up to four JPEG, PNG, GIF or WebP images of at most 5 MB each.", "Open source cards and submit feedback on important answers."]} result="A grounded answer with inspectable evidence, or a clear signal that the available evidence is insufficient." />
            <Subheading id="chat-modes">Choose the right mode</Subheading>
            <Table><thead><tr><TH>Mode</TH><TH>Use it for</TH><TH>Trade-off</TH></tr></thead><tbody><tr><TD>Standard Chat</TD><TD>Precise questions that should retrieve a focused answer quickly.</TD><TD>Narrower investigation.</TD></tr><tr><TD>Deep Dive</TD><TD>Multi-step analysis across broader project evidence.</TD><TD>Longer-running and more resource intensive.</TD></tr></tbody></Table>
            <Subheading id="chat-inputs">Inputs, attachments and Packs</Subheading>
            <ul className="list-disc space-y-2 pl-5"><li>Keep each prompt specific about project, component, period and desired output.</li><li>Images provide visual context; a ZIP source upload is a separate source workflow and can be up to 500 MB.</li><li>Pack actions can request approval before governed output is created or shared.</li><li>Starting a new chat clears conversational context but does not change project access.</li></ul>
            <Subheading id="chat-evidence">Verify before using the answer</Subheading>
            <ol className="list-decimal space-y-2 pl-5"><li>Open every source card supporting a decision-critical claim.</li><li>Distinguish direct evidence from generated interpretation.</li><li>If citations do not support the wording, refine the question or inspect the source directly.</li><li>Treat “no source found” as an evidence gap, not proof that something does not exist.</li></ol>
          </Section>

          <Section id="documents" title="Page reference: Documents">
            <ScreenshotCard src="/guide/screenshots/documents.png" alt="Nexus Documents page" caption="Project selection, upload controls, indexing state and existing evidence documents." />
            <PageReference title="Documents" route="/documents" purpose="Upload project evidence and make it searchable." access="Users with document-ingestion access for the selected project." prerequisites="Know which project owns the file and which document role describes how it may be used." actions={["Select project and document role.", "Upload supported files of at most 50 MB each; multiple files process sequentially.", "Wait for indexing and confirm a non-zero chunk count.", "Validate retrieval from Chat before relying on the upload."]} result="The file shows a completed state and its chunks are retrievable inside the authorized project." />
            <Subheading id="document-roles">Choose the document role deliberately</Subheading>
            <Table><thead><tr><TH>Role</TH><TH>Meaning</TH><TH>Use</TH></tr></thead><tbody><tr><TD>Source</TD><TD>Direct evidence about the project.</TD><TD>Facts, exports, specifications and implementation material.</TD></tr><tr><TD>Reference</TD><TD>Supporting background.</TD><TD>Context that informs but does not directly prove the project state.</TD></tr><tr><TD>Guidance</TD><TD>Recommended way of working.</TD><TD>Practices and interpretation guidance.</TD></tr><tr><TD>Standard / Policy</TD><TD>Normative constraints.</TD><TD>Requirements that generated output or decisions must respect.</TD></tr><tr><TD>Template</TD><TD>Output shape.</TD><TD>Structure and formatting; not factual evidence by itself.</TD></tr></tbody></Table>
            <Subheading id="document-lifecycle">Upload lifecycle</Subheading>
            <p>Queued files process one at a time. Upload success means the file was accepted; indexing success means searchable chunks were produced. Check both status and chunk count before testing Chat.</p>
            <Subheading id="document-delete">Deletion is destructive</Subheading>
            <Callout title="Before deleting" tone="warning">Deleting a document also removes its searchable vectors. Capture any required audit evidence and confirm the file is not the only support for generated content before continuing.</Callout>
          </Section>

          <Section id="project-docs" title="Page reference: Project Docs">
            <ScreenshotCard src="/guide/screenshots/project-docs.png" alt="Nexus Project Docs page" caption="Generated project documentation with selection, document list, and preview." />
            <PageReference title="Project Docs" route="/docs" purpose="Preview and download generated Markdown and PDF documentation." access="Users with access to the selected project." prerequisites="A source generation run must have produced project documents." actions={["Select a project and document.", "Preview Markdown or the generated PDF.", "Generate or regenerate PDFs when available.", "Download the approved MD or PDF file."]} result="A reviewed, downloadable document. Publishing it for Chat retrieval is a separate Push to RAG action under Sources." />
          </Section>

          <Section id="topology" title="Page reference: Topology">
            <ScreenshotCard src="/guide/screenshots/topology.png" alt="Nexus landscape topology with Assistant" caption="Landscape view with grouped systems, integration relationships, and the context-aware Assistant." />
            <PageReference title="Topology" route="/topology" purpose="Explore business and technical relationships while preserving evidence links." access="Users with access to scanned projects." prerequisites="The selected project needs a completed scan; some landscape and runtime overlays need additional generated or operational evidence." actions={["Choose organization landscape, project or technical drilldown.", "Search and filter before following relationships.", "Move from application/integration to job, component and field lineage.", "Open evidence, diagnostics or Page Copilot for context."]} result="A traceable relationship or impact path with enough evidence to support the conclusion—or a visible evidence gap." />
            <Subheading id="topology-model">Mental model</Subheading>
            <p>Start broad and descend only as far as the question requires: organization landscape → application or integration → project → job → component → field lineage. Breadcrumbs preserve the route back up.</p>
            <Subheading id="topology-controls">Controls and overlays</Subheading>
            <ul className="list-disc space-y-2 pl-5"><li>Search, relationship direction, compact mode and fit-to-screen change the view, not the underlying evidence.</li><li>Health and failure focus add operational context to known technical entities.</li><li>Empty levels usually indicate an incomplete or stale scan, unsupported extraction, or missing runtime mapping.</li></ul>
            <Subheading id="topology-evidence">Evidence, diagnostics and Page Copilot</Subheading>
            <p>Use evidence panels to confirm why an entity or edge exists. Diagnostics explain incomplete graph states. Page Copilot can discuss the currently visible topology context, but its answer still needs evidence verification.</p>
          </Section>

          <Section id="profile" title="Page reference: Profile and Security">
            <div className="grid gap-4 lg:grid-cols-2">
              <PageReference title="Profile" route="/settings/profile" purpose="Maintain personal account data and password." access="Current user." actions={["Review username, email, roles and title context.", "Create the first password when required.", "Change an existing password with current-password verification."]} result="Updated profile information or a safely replaced password." />
              <PageReference title="Security" route="/settings/security" purpose="Enrol and maintain the required second factor." access="Current user." actions={["Scan the TOTP QR code.", "Verify the authenticator code.", "Store the ten one-time backup codes safely.", "Disable or replace MFA with password plus authenticator/backup proof."]} result="MFA enabled with a safely stored recovery set." />
            </div>
          </Section>

          {showsRunning("sources") && (
            <Section id="sources" title="Sources and the evidence pipeline">
              <p>Sources are where authorized code and technical artifacts enter Nexus. Project Docs and Chat are only as current as the most recent successful pipeline stages.</p>
              <Subheading id="source-pipeline">Connect → Scan → Generate → Push</Subheading>
              <EvidenceLifecycle />
              <PageReference title="Sources" route="/settings/sources" purpose="Connect GitHub or ZIP sources and operate the project evidence pipeline." access="Owner, Admin or project owner in the supported UI; actual actions remain permission- and project-scoped." prerequisites="Confirm repository authority, project identifier, platform, intended owner and least-privilege credentials." actions={["Connect a GitHub App installation, token-based repository or ZIP archive.", "Run Scan and inspect extracted counts, timestamps and logs.", "Generate governed project documentation.", "Review output, then Push to RAG so Chat can retrieve it.", "Maintain webhook status and rotate credentials when required."]} result="A current scan, reviewed generated documents and intentionally published retrieval evidence." />
              <Subheading id="source-controls">Controls and status meanings</Subheading>
              <Table><thead><tr><TH>Control</TH><TH>What it changes</TH><TH>Check afterward</TH></tr></thead><tbody><tr><TD>Scan</TD><TD>Re-extracts technical facts from the connected source.</TD><TD>Status, completion time, counts and scan logs.</TD></tr><tr><TD>Generate docs</TD><TD>Creates documentation from scanned facts plus the active Pack.</TD><TD>Document list, warnings and unsupported claims.</TD></tr><tr><TD>Push to RAG</TD><TD>Indexes approved generated output for Chat retrieval.</TD><TD>Publish status and a small set of cited test questions.</TD></tr><tr><TD>Reset status</TD><TD>Clears a stuck pipeline state.</TD><TD>Root cause before retrying the operation.</TD></tr><tr><TD>Rotate token</TD><TD>Replaces stored repository credentials.</TD><TD>Connection and webhook tests.</TD></tr></tbody></Table>
              <Subheading id="source-safety">Safe operations</Subheading>
              <Callout title="Deletion and credentials" tone="warning">Deleting a source or project can remove access to scan history and generated artifacts. Never expose repository tokens in Chat, screenshots or logs; rotate immediately after suspected exposure.</Callout>
            </Section>
          )}

          {showsRunning("admin-pages") && (
            <Section id="admin-pages" title="Users, access and projects">
              <p>Members, access requests, roles and project assignments share one page: <a className="font-medium text-primary hover:underline" href="/admin/organization">Organization</a>. Each tab below is a section of that dashboard, and each is gated by its own permission.</p>
              <Table>
                <thead><tr><TH>Tab</TH><TH>Required authority</TH><TH>Purpose and safe use</TH></tr></thead>
                <tbody>
                  <tr><TD><a className="font-medium text-primary hover:underline" href="/admin/organization?tab=members">Members</a></TD><TD><code>manage_users</code></TD><TD>Create/edit/deactivate ordinary members, change their roles and reset passwords. Owner rows are read-only and protected.</TD></tr>
                  <tr><TD><a className="font-medium text-primary hover:underline" href="/admin/organization?tab=access">Access requests</a></TD><TD><code>manage_users</code></TD><TD>Approve with a role, or reject; resend or revoke invitations; test delivery before assuming user error. Pre-assigning projects at approval additionally requires <code>assign_projects</code>.</TD></tr>
                  <tr><TD><a className="font-medium text-primary hover:underline" href="/admin/organization?tab=projects">Projects</a></TD><TD><code>create_projects</code> / <code>assign_projects</code></TD><TD>Create projects, assign members and maintain the single project-owner assignment. Normal unassign cannot remove that owner.</TD></tr>
                  <tr><TD><a className="font-medium text-primary hover:underline" href="/admin/organization?tab=roles">Roles</a></TD><TD>Owner for definitions</TD><TD>Admins may view role choices; only Owner can create, edit or delete definitions and list the full permission catalog.</TD></tr>
                </tbody>
              </Table>
              <Callout title="Admin is deliberately broader than Developer" tone="success">Admin handles ordinary member access and owned-project operations. The boundary stays below protected Owner accounts, role-definition authority and global security exceptions.</Callout>
            </Section>
          )}

          {showsRunning("observability") && (
            <Section id="observability" title="Observe and diagnose">
              <p>Operational pages answer different questions. Visibility follows the relevant audit/runtime permissions, even when the default Admin role normally exposes them.</p>
              <Subheading id="observe-usage">Usage and cost</Subheading>
              <PageReference title="Usage" route="/admin/usage" purpose="Analyze tokens, cost, latency and activity by interface or user." access="Users with audit-log visibility." actions={["Choose 7, 30 or 90 days.", "Move between Overview, Per User and User Detail.", "Inspect recent threads and documentation generation.", "Separate volume, latency and cost before drawing conclusions."]} result="An evidence-backed explanation of where usage and cost originate." />
              <Subheading id="observe-runtime">Logs and runtime sources</Subheading>
              <div className="grid gap-4 lg:grid-cols-2"><PageReference title="Operational Logs" route="/admin/logs" purpose="Inspect runs, jobs, plans, incidents, performance, signatures and estate health." access="Audit-log permission." actions={["Start at Overview.", "Filter the relevant subview.", "Open the execution or incident detail.", "Check freshness before escalating."]} result="A traceable runtime symptom, affected entity and next diagnostic step." /><PageReference title="Runtime Sources" route="/admin/runtime" purpose="Configure operational providers and workspace mappings." access="Runtime read/write permissions." actions={["Add or inspect a provider.", "Set polling and enablement.", "Run manual sync.", "Rotate credentials and verify mappings."]} result="A healthy provider with current sync and auditable credential handling." /></div>
              <Subheading id="observe-quality">Traces and memory</Subheading>
              <Table><thead><tr><TH>Page</TH><TH>Use it for</TH><TH>Do not use it as</TH></tr></thead><tbody><tr><TD><a className="font-medium text-primary hover:underline" href="/admin/agent-traces">Agent Traces</a></TD><TD>Follow route, tool, workflow and final outcomes filtered by status or tool.</TD><TD>A replacement for user-visible answer evidence.</TD></tr><tr><TD><a className="font-medium text-primary hover:underline" href="/admin/hindsight">Memory</a></TD><TD>Inspect memory banks, recent memories and reflection state.</TD><TD>A place to store credentials or bypass project evidence rules.</TD></tr></tbody></Table>
            </Section>
          )}

          <Section id="workflows" title="Common workflows">
            <div className="grid gap-4 lg:grid-cols-2">
              {[
                { title: "Join Nexus", before: "A valid invitation or approved access request and access to your mailbox and authenticator.", steps: ["Open the setup link or use its email code.", "Complete profile onboarding.", "Enrol MFA and save all ten backup codes.", "Create the first password.", "Confirm your assigned projects."], result: "MFA and password are configured and only the intended project scope is visible." },
                { title: "Answer a project question", before: "The project is assigned and its evidence is indexed.", steps: ["Open Chat and select the project.", "Choose Standard or Deep Dive.", "Ask one precise question.", "Open citations for critical claims.", "Record feedback or refine when evidence is weak."], result: "A cited answer whose important claims you verified against authorized evidence." },
                { title: "Add searchable evidence", before: "The correct project, document role and non-sensitive file of at most 50 MB.", steps: ["Open Documents.", "Select project and role.", "Upload the file.", "Wait for completed indexing and a non-zero chunk count.", "Test retrieval in Chat."], result: "The new evidence is retrievable and classified correctly." },
                { title: "Onboard a project", before: "Identifier, platform, project owner, source authority, credentials and intended Pack are agreed.", steps: ["Create the project.", "Connect GitHub or upload ZIP.", "Scan and inspect logs/counts.", "Review topology and gaps.", "Generate docs with the intended Pack.", "Review, push to RAG and test citations."], result: "A managed project with current evidence, reviewed docs and validated retrieval." },
              ].map(({ title, before, steps, result }) => (
                <article key={title as string} className="rounded-lg border border-border p-5">
                  <h3 className="font-semibold text-foreground">{title}</h3>
                  <p className="mt-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Before you begin</p>
                  <p className="mt-1 text-sm leading-6">{before}</p>
                  <ol className="mt-4 list-decimal space-y-1 pl-5 text-sm">{steps.map((step) => <li key={step}>{step}</li>)}</ol>
                  <div className="mt-4 border-t border-border/60 pt-3"><p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Done when</p><p className="mt-1 text-sm leading-6">{result}</p></div>
                </article>
              ))}
            </div>
          </Section>

          {showsRunning("admin-runbook") && (
            <Section id="admin-runbook" title="Admin runbook">
              <div className="grid gap-4 sm:grid-cols-2">
                {[
                  ["Daily", ["Review pending access requests.", "Check failed scans and runtime evidence.", "Confirm uploads completed indexing.", "Escalate Owner-only work."]],
                  ["Before adding a project", ["Confirm identifier, platform, and intended owner.", "Confirm repository authority and least-privilege credentials.", "Identify required Developers.", "Agree which outputs require review."]],
                  ["After scan or generation", ["Check status, timestamps, jobs, and documents.", "Confirm topology completeness.", "Review unsupported claims.", "Test a small set of cited questions."]],
                  ["Access review", ["Review active users and invitations.", "Remove access that is no longer needed.", "Confirm one project owner per project.", "Never share the Owner account."]],
                ].map(([title, items]) => (
                  <article key={title as string} className="rounded-lg border border-border p-5">
                    <h3 className="mb-3 font-semibold text-foreground">{title as string}</h3>
                    <ul className="space-y-2 text-sm">{(items as string[]).map((item) => <li key={item} className="flex gap-2"><span className="text-primary">□</span>{item}</li>)}</ul>
                  </article>
                ))}
              </div>
            </Section>
          )}

          <Section id="security" title="Security and recovery">
            <Table>
              <thead><tr><TH>Situation</TH><TH>Safe response</TH><TH>Who acts</TH></tr></thead>
              <tbody>
                <tr><TD>No password exists</TD><TD>Use email-code sign-in, complete MFA, then create the first password.</TD><TD>User</TD></tr>
                <tr><TD>Existing password forgotten</TD><TD>Ask an Admin or Owner for an audited reset. After reset, sign in with the temporary password and replace it according to organization policy.</TD><TD>User + Admin/Owner</TD></tr>
                <tr><TD>Authenticator unavailable</TD><TD>Use one backup code and enrol a replacement authenticator.</TD><TD>User</TD></tr>
                <tr><TD>No authenticator or backup code</TD><TD>Use the verified organization recovery process.</TD><TD>Owner</TD></tr>
                <tr><TD>Credential exposure</TD><TD>Change password, replace MFA, rotate affected source credentials, and review audit evidence.</TD><TD>User + authorized operator</TD></tr>
                <tr><TD>Member leaves</TD><TD>Deactivate the account, revoke invitations, and review project assignments.</TD><TD>Owner or Admin</TD></tr>
              </tbody>
            </Table>
            <Callout title="Credential handling" tone="warning">
              Never paste passwords, TOTP secrets, backup codes, GitHub tokens, or runtime API keys into Chat, documents, generated output, or support screenshots.
            </Callout>
          </Section>

          <Section id="troubleshooting" title="Troubleshooting">
            <div className="space-y-3">
              {[
                ["This account does not have a password", "Use email code instead, complete MFA, then create the required first password."],
                ["The email code does not arrive", "Check spelling, spam/quarantine, wait briefly, and resend once. A new code invalidates older active codes. The Owner can inspect email configuration."],
                ["A code is rejected", "Email codes expire after 10 minutes and allow five attempts. Use the newest code. For TOTP, confirm device time is accurate; an unused backup code works at MFA."],
                ["I am redirected to Security", "This is expected when MFA is not configured. Other routes remain locked until enrolment succeeds."],
                ["A project is missing", "Ask the project owner to verify assignment and source status. Developers only see assigned projects."],
                ["Chat returns no useful sources", "Confirm project selection, indexed files, chunk count, question specificity, and whether approved docs were pushed to RAG."],
                ["An Admin action is absent", "Admin navigation is permission-driven and project operations can also require ownership. Owner, role-definition and global-security actions remain Owner-only."],
                ["Scan completed but Topology is incomplete", "Inspect scan logs and extracted counts, confirm the correct branch/source, then rescan only after resolving the evidence gap."],
                ["Generated docs are missing from Chat", "Project Docs preview is not publication. Review the output, then use Push to RAG under Sources and test retrieval again."],
                ["I forgot an existing password", "The first-password flow only applies when a password never existed. Request the approved Admin/Owner reset."],
              ].map(([question, answer]) => (
                <details key={question} className="group rounded-lg border border-border bg-card open:bg-muted/40">
                  <summary className="cursor-pointer list-none px-5 py-4 font-medium text-foreground marker:hidden">{question}</summary>
                  <p className="border-t border-border px-5 py-4 text-sm leading-6">{answer}</p>
                </details>
              ))}
            </div>
          </Section>

          <Section id="glossary" title="Glossary">
            <Table>
              <thead><tr><TH>Term</TH><TH>Meaning in Nexus</TH></tr></thead>
              <tbody>
                <tr><TD>Owner</TD><TD>The one global organization authority, internally represented by is_superadmin.</TD></tr>
                <tr><TD>Admin</TD><TD>Day-to-day operator for ordinary members, access, and projects without Owner-only authority.</TD></tr>
                <tr><TD>Project owner</TD><TD>The single assignment allowed to administer one project.</TD></tr>
                <tr><TD>Developer</TD><TD>Default project-user role for assigned-project work without administration.</TD></tr>
                <tr><TD>RAG</TD><TD>Retrieval-augmented generation: authorized evidence is retrieved before a grounded answer is produced.</TD></tr>
                <tr><TD>Chunk</TD><TD>A searchable section extracted from an uploaded or generated document.</TD></tr>
                <tr><TD>Scan</TD><TD>Extraction of technical facts from an authorized project source.</TD></tr>
                <tr><TD>Push to RAG</TD><TD>Index approved generated docs so Chat can retrieve them.</TD></tr>
                <tr><TD>MFA / 2FA</TD><TD>Required second factor using TOTP or a one-time backup code.</TD></tr>
                <tr><TD>Email code</TD><TD>Six-digit mailbox code for passwordless sign-in; it is not the authenticator code.</TD></tr>
              </tbody>
            </Table>
          </Section>

          <footer className="border-t border-border py-10 text-xs leading-6 text-muted-foreground">
            <p className="font-medium text-muted-foreground">Nexus Product Guide · Last verified {LAST_VERIFIED} · IntoData</p>
            <p>Verified against the current React routes, supported UI workflows and RBAC implementation. Screenshots are illustrative; controls and authority descriptions take precedence.</p>
          </footer>
        </main>
      </div>
    </div>
  );
}
