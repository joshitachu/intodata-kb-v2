import type { Persona } from "@/types/onboarding";
import type { TourDefinition, TourStep } from "./types";

/**
 * Role-aware walkthrough content.
 *
 * The previous tour was a single seven-step list shown to everybody, so an
 * Owner was taught nothing about operating the deployment and a developer was
 * walked past admin surfaces they cannot open. Each persona now gets a tour
 * built from shared "how the product works" steps plus the steps that match
 * what that person is actually responsible for.
 *
 * Ordering principle: teach the loop that produces value first (ask a question,
 * read the citation), then where evidence comes from, then administration. A
 * new member should be able to stop after the first chapter and still be able
 * to use the product.
 *
 * On `target`: it is a comma-separated *preference list*, resolved left to
 * right by `resolveTarget`, preferring a candidate that actually has a box.
 * Steps used to point at page-root containers, so the "spotlight" cutout was
 * the entire page with a border round it — it highlighted nothing. Each step
 * now names the specific control it is talking about, and falls back to an
 * always-rendered ancestor for the empty-deployment case where that control
 * has not been rendered yet. A step with no fallback is one whose subject
 * genuinely does not exist without data; it is skipped rather than pointed at
 * something else and described wrongly.
 *
 * On copy: every claim here is checked against the component that renders it.
 * Control names, option labels and limits are quoted from the source, not
 * remembered. A tour that describes a button which is not there is worse than
 * no tour, because the reader concludes the product is broken rather than that
 * the tour is stale.
 *
 * On length: chapters, not steps, are the unit the card's progress bar shows,
 * and each chapter can be skipped from the card. An owner sees seven chapters;
 * clicking through all of them is a choice, not a toll.
 */

// --- Shared chapters -------------------------------------------------------

/** The core value loop. Every persona sees these, in this order. */
const ASKING: TourStep[] = [
  {
    id: "chat.input",
    target: '[data-tour="chat-input"]',
    route: "/",
    chapter: "Asking questions",
    title: "Ask in plain language",
    body:
      "Describe what you want to know the way you would ask a colleague. No query syntax, no keywords — the assistant searches your documentation and code evidence.",
    tryIt: {
      label: "Try it after the tour",
      hint: 'Something like "which jobs write to the billing database?" works better than a keyword.',
    },
    side: "top",
  },
  {
    id: "chat.project-scope",
    // The selector renders nothing at all when no project exists, so the
    // composer toolbar around it is the fallback.
    target: '[data-tour="project-selector"], [data-tour="chat-toolbar"]',
    route: "/",
    chapter: "Asking questions",
    title: "Answers are scoped to a project",
    body:
      "This picker decides which evidence can be searched; All Projects searches everything you are allowed to see. If an answer looks thin, check this first — it is the most common cause.",
    side: "top",
  },
  {
    id: "chat.modes",
    target: '[data-tour="mode-toggle"]',
    route: "/",
    chapter: "Asking questions",
    title: "Two speeds, on purpose",
    body:
      "Chat answers from evidence that is already indexed. Deep Dive investigates a single scanned project in depth — it needs a project selected before the box will accept a question.",
    side: "top",
  },
];

/** Where answers come from — what makes the output auditable instead of magic. */
const EVIDENCE: TourStep[] = [
  {
    id: "nav.sidebar",
    target: '[data-tour="sidebar"]',
    route: "/",
    chapter: "Where answers come from",
    title: "Everything else is in the sidebar",
    body:
      "Documents, Project Docs and Topology are the three evidence surfaces. Admin appears when your role includes it, and New Chat starts a fresh thread without losing the old ones.",
    side: "right",
  },
  {
    id: "evidence.docs",
    target: '[data-tour="docs-header"], [data-tour="project-docs-page"]',
    route: "/docs",
    chapter: "Where answers come from",
    title: "Generated project documentation",
    body:
      "Scanning a source writes Markdown documentation for it. Choose a project here to browse what was generated — these pages are what most answers end up citing.",
    side: "bottom",
  },
  {
    id: "evidence.docs-pdf",
    // No fallback: this control only exists once a project has documents, and
    // describing it over an empty page would be a lie.
    target: '[data-tour="docs-doc-list"]',
    route: "/docs",
    chapter: "Where answers come from",
    title: "Every document can become a PDF",
    body:
      "Generate PDFs converts the listed documents in one pass, and reads Regenerate once they all have one. When a project holds several sub-projects, filter the list by sub-project first.",
    side: "right",
  },
  {
    id: "evidence.documents-role",
    target: '[data-tour="documents-role"], [data-tour="documents-page"]',
    route: "/documents",
    chapter: "Where answers come from",
    title: "Say which project, and what kind of file",
    body:
      "Every upload is filed against one project. The Role — Source, Reference, Guidance, Standard, Template or Policy — travels with the file onto its citations, so a policy is never read as an implementation.",
    guard: { permissions: ["ingest_documents"] },
    side: "bottom",
  },
  {
    id: "evidence.documents",
    target: '[data-tour="documents-upload"], [data-tour="documents-page"]',
    route: "/documents",
    chapter: "Where answers come from",
    title: "Your own uploads sit alongside",
    body:
      "Drop PDF, DOCX, PPTX, XLSX or MD files, up to 50 MB each. They are indexed into the same knowledge base as your scanned code, so a design decision and the job that implements it can be cited together.",
    guard: { permissions: ["ingest_documents"] },
    side: "bottom",
  },
  {
    id: "evidence.topology",
    target: '[data-tour="topology-header"], [data-tour="topology-page"]',
    route: "/topology",
    chapter: "Where answers come from",
    title: "See the landscape, not just text",
    body:
      "Pick a project to draw its systems, jobs, routes and the dependencies between them. Select any node to drill in. Follow conversation lets the assistant move the view to whatever it is describing.",
    side: "bottom",
  },
];

const CLOSING: TourStep = {
  id: "closing.guide",
  chapter: "You are set up",
  title: "That is the whole loop",
  body:
    "Ask, read the citation, follow it to the source. The full reference lives under Help in the sidebar, and you can restart this walkthrough from there at any time.",
  side: "center",
};

// --- Administration chapters ----------------------------------------------

/** Member access. Only meaningful to someone who can actually grant it. */
const ACCESS_ADMIN: TourStep[] = [
  {
    id: "admin.access",
    // Scoped by tab on purpose: the table anchor exists in whichever panel is
    // open, so without the tab prefix this would resolve against the members
    // table before the tab has actually changed.
    target:
      '[data-tour="admin-access"] [data-tour="admin-org-table"], [data-tour="admin-access"]',
    route: "/admin/organization?tab=access",
    chapter: "Running the workspace",
    title: "Approve access with a role, not after it",
    body:
      "Every request is approved as a specific role. Choosing Admin here is what grants member administration — decide it now rather than promoting someone later.",
    guard: { permissions: ["manage_users"] },
    side: "bottom",
  },
  {
    id: "admin.members",
    target:
      '[data-tour="admin-members"] [data-tour="admin-org-table"], [data-tour="admin-members"]',
    route: "/admin/organization?tab=members",
    chapter: "Running the workspace",
    title: "Members and their project scope",
    body:
      "Roles decide what someone can operate; project assignments decide what they can see. Both are changed from this tab, per member.",
    guard: { permissions: ["manage_users"] },
    side: "bottom",
  },
  {
    id: "admin.roles",
    target:
      '[data-tour="admin-roles"] [data-tour="admin-org-table"], [data-tour="admin-roles"]',
    route: "/admin/organization?tab=roles",
    chapter: "Running the workspace",
    title: "Roles are defined once, centrally",
    body:
      "A role is a named set of permissions. Editing the definition changes it for everyone who holds it, which is why this tab is restricted to the workspace owner.",
    guard: { permissions: ["manage_roles"] },
    side: "bottom",
  },
  {
    id: "admin.projects",
    target:
      '[data-tour="admin-projects"] [data-tour="admin-org-table"], [data-tour="admin-projects"]',
    route: "/admin/organization?tab=projects",
    chapter: "Running the workspace",
    title: "Projects, and who is assigned to them",
    body:
      "Every project is listed here with its member assignments. This is where someone is added to a project or taken off one, which is what their answers are scoped by.",
    guard: { permissions: ["manage_projects"] },
    side: "bottom",
  },
];

/**
 * Getting evidence into the system. The project-owner and admin job.
 *
 * Deliberately one step per connection method. The cards on this page do
 * genuinely different things — an installed app, a stored token, a file upload
 * — and collapsing them into a single "connect a source" step is what left
 * people unsure which one applied to them.
 */
const SOURCES_ADMIN: TourStep[] = [
  {
    id: "sources.github",
    target: '[data-tour="sources-github"], [data-tour="sources-page"]',
    route: "/settings/sources",
    chapter: "Connecting your code",
    title: "GitHub, through an installed app",
    body:
      "Install the GitHub App once per account or organization, then pick what to bring in with Choose repositories. No token is stored, and importing a repository never starts a scan.",
    guard: { permissions: ["manage_projects"] },
    side: "bottom",
  },
  {
    id: "sources.azure",
    target: '[data-tour="sources-azure"], [data-tour="sources-page"]',
    route: "/settings/sources",
    chapter: "Connecting your code",
    title: "Azure DevOps, through a stored token",
    body:
      "Paste an organization URL and a personal access token once. Nexus then lists every repository that token can read and clones them with it. Connect another organization at any time.",
    guard: { permissions: ["manage_projects"] },
    side: "bottom",
  },
  {
    id: "sources.manual",
    target: '[data-tour="sources-add"], [data-tour="sources-page"]',
    route: "/settings/sources",
    chapter: "Connecting your code",
    title: "Or upload an archive directly",
    body:
      "Add manually takes a ZIP archive, or a repository address plus an access token. This is the route when the server has no GitHub App installed and no Azure DevOps connection.",
    guard: { permissions: ["manage_projects"] },
    side: "bottom",
  },
  {
    id: "sources.live",
    target: '[data-tour="sources-live"], [data-tour="sources-page"]',
    route: "/settings/sources",
    chapter: "Connecting your code",
    title: "Live evidence is a separate connection",
    body:
      "Code tells you what was built. Connect Elastic, and the scheduled runtime mirrors listed underneath it, to also record what actually ran — that is what runtime answers are grounded in.",
    guard: { permissions: ["manage_projects"] },
    side: "bottom",
  },
  {
    id: "sources.lifecycle",
    // The status column is hidden below 640px, where it measures 0x0 and the
    // resolver skips past it to the section header.
    target: '[data-tour="sources-lifecycle"], [data-tour="sources-summary"]',
    route: "/settings/sources",
    chapter: "Connecting your code",
    title: "One button at a time, on purpose",
    body:
      "A source reads Not scanned yet, then Scanned no docs, then Docs ready, then In the knowledge base. Each row offers only its next action, so nothing is published without someone choosing to.",
    guard: { permissions: ["manage_projects"] },
    side: "left",
    padding: 10,
  },
];

/** Cost and volume. Owner-facing. */
const USAGE_OWNER: TourStep[] = [
  {
    id: "owner.usage",
    target: '[data-tour="admin-usage-kpis"], [data-tour="admin-usage"]',
    route: "/admin/usage",
    chapter: "Cost and usage",
    title: "Cost and query volume",
    body:
      "Total Queries, Input and Output Tokens, Total Cost and Active Users, for the period chosen at the top right — last 7, 30 or 90 days. Every number on this page follows that selector.",
    guard: { permissions: ["view_audit_logs"] },
    side: "bottom",
    padding: 10,
  },
  {
    id: "usage.tabs",
    target: '[data-tour="admin-usage-tabs"], [data-tour="admin-usage"]',
    route: "/admin/usage",
    chapter: "Cost and usage",
    title: "The whole deployment, or one person",
    body:
      "Overview totals everybody. Per User breaks the same period down by person, and selecting someone there opens a User Detail tab holding only their activity.",
    guard: { permissions: ["view_audit_logs"] },
    side: "bottom",
  },
  {
    id: "usage.trends",
    target: '[data-tour="admin-usage-trends"], [data-tour="admin-usage"]',
    route: "/admin/usage",
    chapter: "Cost and usage",
    title: "Daily queries next to daily cost",
    body:
      "Two lines over the selected period: how many questions were asked, and what they cost. Read them together — cost climbing faster than volume means each question got more expensive.",
    guard: { permissions: ["view_audit_logs"] },
    side: "bottom",
  },
  {
    id: "usage.interfaces",
    target: '[data-tour="admin-usage-breakdown"], [data-tour="admin-usage"]',
    route: "/admin/usage",
    chapter: "Cost and usage",
    title: "Which surface spent the money",
    body:
      "Cost by Interface splits spend by the part of the product that did the spending. The table beside it adds query counts and token counts per interface, so a rise can be traced to its cause.",
    guard: { permissions: ["view_audit_logs"] },
    side: "top",
  },
];

/**
 * Runtime execution evidence. Owner-facing.
 *
 * This chapter replaces a single step that described /admin/logs as an
 * application audit trail — sign-ins, role grants, access approvals. That page
 * has never been that: it is imported runtime execution history. There is no
 * audit-log UI in the product, so no step claims one exists.
 */
const RUNTIME_OWNER: TourStep[] = [
  {
    id: "runtime.what",
    target: '[data-tour="runtime-header"], [data-tour="admin-logs"]',
    route: "/admin/logs",
    chapter: "Runtime evidence",
    title: "What ran, what failed, and why",
    body:
      "Execution history imported from your runtime — not a record of who changed what in Nexus. The badge beside the title says how fresh the import is, because data arrives on a poll.",
    guard: { permissions: ["view_audit_logs"] },
    side: "bottom",
  },
  {
    id: "runtime.views",
    target: '[data-tour="admin-logs-views"], [data-tour="admin-logs"]',
    route: "/admin/logs",
    chapter: "Runtime evidence",
    title: "Ten views over the same evidence",
    body:
      "Overview is the summary you are on. Runs, Jobs, Routes, Data services, Plans, Incidents, Performance, Signatures and Estate each slice the same imported executions a different way.",
    guard: { permissions: ["view_audit_logs"] },
    side: "bottom",
  },
  {
    id: "runtime.health",
    target: '[data-tour="runtime-kpis"], [data-tour="admin-logs"]',
    route: "/admin/logs",
    chapter: "Runtime evidence",
    title: "Four numbers, one time window",
    body:
      "Runs and Failure rate cover the window you picked. Never finished counts runs with no end time. Engines online is not windowed — it is the state of the runtime right now.",
    guard: { permissions: ["view_audit_logs"] },
    side: "bottom",
  },
  {
    id: "runtime.evidence",
    // No fallback: these tiles only exist when the window contains failures.
    // With none, the panel says so instead, and this step is skipped.
    target: '[data-tour="runtime-evidence"]',
    route: "/admin/logs",
    chapter: "Runtime evidence",
    title: "How much of the failure is explainable",
    body:
      "Failure logs counts failures that captured any logs. Useful diagnoses counts those the evidence actually supports. Still unexplained is left standing rather than filled with a guessed cause.",
    guard: { permissions: ["view_audit_logs"] },
    side: "bottom",
  },
];

// --- Per-persona composition ----------------------------------------------

const TOURS: Record<Persona, TourDefinition> = {
  developer: {
    variant: "developer",
    label: "Getting started",
    pitch: "How to ask, and how to check the answer.",
    steps: [...ASKING, ...EVIDENCE, CLOSING],
  },
  project_owner: {
    variant: "project_owner",
    label: "Getting started",
    pitch: "How to ask, how evidence gets in, and what you own.",
    steps: [...ASKING, ...EVIDENCE, ...SOURCES_ADMIN, CLOSING],
  },
  admin: {
    variant: "admin",
    label: "Admin walkthrough",
    pitch: "The product loop first, then access and evidence administration.",
    steps: [...ASKING, ...EVIDENCE, ...SOURCES_ADMIN, ...ACCESS_ADMIN, CLOSING],
  },
  owner: {
    variant: "owner",
    label: "Owner walkthrough",
    pitch: "Everything an admin sees, plus deployment cost and runtime evidence.",
    steps: [
      ...ASKING,
      ...EVIDENCE,
      ...SOURCES_ADMIN,
      ...ACCESS_ADMIN,
      ...USAGE_OWNER,
      ...RUNTIME_OWNER,
      CLOSING,
    ],
  },
};

export function tourFor(persona: Persona): TourDefinition {
  return TOURS[persona] ?? TOURS.developer;
}

export const ALL_TOURS = TOURS;
