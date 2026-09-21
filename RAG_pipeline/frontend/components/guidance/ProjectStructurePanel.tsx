"use client";

import { useMemo } from "react";
import { AlertTriangle, ChevronDown, FolderTree } from "lucide-react";
import { useChatContext } from "@/contexts/ChatContext";
import { useDocsProjects } from "@/hooks/useDocsProjects";
import { usePermissions } from "@/hooks/usePermissions";
import {
  guidanceErrorMessage,
  useApproveProjectRule,
  useCreateOrgRule,
  useCreateProjectRule,
  useGuidancePreview,
  useOrgGuidance,
  useProjectGuidance,
  useRetireOrgRule,
  useRetireProjectRule,
} from "@/hooks/useGuidance";
import { cn } from "@/lib/utils";
import {
  LAYER_TOKEN_BUDGETS,
  WORKSPACE_PATH_TO_LAYER,
  workspaceOriginDocumentId,
  type GuidanceLayer,
  type GuidanceRuleCreateRequest,
  type ProjectWorkspaceResponse,
  type WorkspaceFile,
} from "@/types/guidance";
import { BudgetMeter } from "./BudgetMeter";
import { PreferencesEditor } from "./PreferencesEditor";
import { ProjectMaterialSection } from "./ProjectMaterialSection";
import { RulesFileEditor } from "./RulesFileEditor";
import { WorkspaceTree } from "./WorkspaceTree";
import {
  ORG_KINDS,
  ORG_KIND_HINTS,
  PROJECT_KINDS,
  PROJECT_KIND_HINTS,
} from "./kindLabels";
import { useProjectWorkspace } from "./workspaceQueries";

/**
 * Everything the assistant is told, and every way to change it.
 *
 * WHAT THIS PANEL IS FOR
 *
 * Someone asks the assistant a question and gets an answer they did not expect.
 * This is where they find out why, and where they fix it.
 *
 * ONE PLACE, NOT TWO
 *
 * The panel used to be a file tree with a viewer, and then, below it, four
 * editor cards -- organization, project, preferences, material -- each with its
 * own sub-sections, its own add form and its own budget meter. Roughly fifteen
 * boxes describing the same four files the tree was already listing. A reader
 * had to hold two copies of the stack in their head and keep them in sync
 * themselves.
 *
 * So the tree is the navigation and the pane beside it is the file, including
 * the controls that write it. Select `.nexus/org/RULES.md` and you read what is
 * in it and add to it in one place; select `AGENTS.md` and there is nothing to
 * edit, because nothing edits it. Nothing is rendered below that grid.
 *
 * The project selector lives in this panel and not only in the upload panel
 * next door. Both read and write the same selection, so they can never
 * disagree -- but a panel whose entire content depends on a control somewhere
 * else opens empty and looks broken, which is exactly what it did.
 */

const OWNER_ONLY_REASON =
  "Only the deployment owner can change organization rules, because they apply to every project and every person.";

const MANAGE_PROJECTS_REASON =
  "Changing this needs the 'manage projects' permission, because it changes answers for everyone on the project. Ask an administrator for it.";

/** Where each level is stored. The pane keys its editor off these paths. */
const LAYER_WORKSPACE_PATHS: Record<GuidanceLayer, string> = {
  org: ".nexus/org/RULES.md",
  project: ".nexus/project/RULES.md",
  person: ".nexus/user/PREFERENCES.md",
};

/** The origin the backend stamps on each level's file. */
const LAYER_ORIGINS: Record<GuidanceLayer, string> = {
  org: "org_layer",
  project: "project_layer",
  person: "person_layer",
};

/** Human name of each level, used by the budget meter's accessible label. */
const LEVEL_BUDGET_LABELS: Record<GuidanceLayer, string> = {
  org: "Organization rules",
  project: "Project rules",
  person: "My preferences",
};

/**
 * The level files, listed whether or not the backend wrote them.
 *
 * A level that renders empty is not projected at all -- `project_workspace_files`
 * skips it -- so a project with no rules yet has no `.nexus/project/RULES.md`
 * and a user who has set no preferences has no `.nexus/user/PREFERENCES.md`.
 * With editing living in the file pane, a path missing from the tree is a path
 * nobody can ever write the first line of, which would make a fresh project
 * permanently uneditable.
 *
 * So the three paths are always listed. The ones the server did not send are
 * marked as not written yet, kept out of the cost counts and out of both cost
 * filters, and open onto the same editor -- adding the first entry is what
 * brings the real file into being.
 */
function withLevelFiles(workspace: ProjectWorkspaceResponse | undefined): {
  workspace: ProjectWorkspaceResponse | undefined;
  pending: ReadonlySet<string>;
} {
  if (!workspace) return { workspace, pending: new Set() };

  const present = new Set(workspace.files.map((file) => file.path));
  const missing = (Object.keys(LAYER_WORKSPACE_PATHS) as GuidanceLayer[])
    .map((layer) => ({ layer, path: LAYER_WORKSPACE_PATHS[layer] }))
    .filter((entry) => !present.has(entry.path));

  if (missing.length === 0) return { workspace, pending: new Set() };

  const placeholders: WorkspaceFile[] = missing.map(({ layer, path }) => ({
    path,
    origin: LAYER_ORIGINS[layer],
    always_loaded: false,
    bytes: 0,
    content: "",
  }));

  return {
    workspace: { ...workspace, files: [...workspace.files, ...placeholders] },
    pending: new Set(missing.map((entry) => entry.path)),
  };
}

export function ProjectStructurePanel() {
  const { selectedProject, setSelectedProject } = useChatContext();
  const { hasPermission, isOwner } = usePermissions();
  const canManageProject = hasPermission("manage_projects");

  const org = useOrgGuidance("active");
  const projectActive = useProjectGuidance(selectedProject, "active");
  const projectProposed = useProjectGuidance(selectedProject, "proposed");
  const preview = useGuidancePreview(selectedProject);
  const workspace = useProjectWorkspace(selectedProject);

  const createOrgRule = useCreateOrgRule();
  const retireOrgRule = useRetireOrgRule();
  const createProjectRule = useCreateProjectRule(selectedProject);
  const retireProjectRule = useRetireProjectRule(selectedProject);
  const approveProjectRule = useApproveProjectRule(selectedProject);

  const listed = useMemo(() => withLevelFiles(workspace.data), [workspace.data]);

  const orgReason = isOwner ? null : OWNER_ONLY_REASON;
  const projectReason = canManageProject ? null : MANAGE_PROJECTS_REASON;

  const tokensFor = (layer: GuidanceLayer): number | null =>
    preview.data ? (preview.data.layer_tokens[layer] ?? 0) : null;
  const ceilingFor = (layer: GuidanceLayer): number =>
    preview.data?.layer_budgets[layer] ?? LAYER_TOKEN_BUDGETS[layer];

  async function addOrgRule(draft: GuidanceRuleCreateRequest): Promise<boolean> {
    try {
      await createOrgRule.mutateAsync(draft);
      return true;
    } catch {
      // Surfaced by the notice beside the form, including the budget refusal.
      return false;
    }
  }

  async function addProjectRule(draft: GuidanceRuleCreateRequest): Promise<boolean> {
    try {
      await createProjectRule.mutateAsync(draft);
      return true;
    } catch {
      return false;
    }
  }

  /** The room left in the file on screen, reported in its own pane. */
  function paneBudget(file: WorkspaceFile) {
    const layer = WORKSPACE_PATH_TO_LAYER[file.path];
    if (!layer) return null;
    return (
      <BudgetMeter
        layerLabel={LEVEL_BUDGET_LABELS[layer]}
        used={tokensFor(layer)}
        ceiling={ceilingFor(layer)}
      />
    );
  }

  /**
   * Whatever writes the file on screen, or nothing when nothing does.
   *
   * Keyed off the path rather than off a tab the user picked, so the editor and
   * the text above it can never be about different files.
   */
  function paneEditor(file: WorkspaceFile) {
    const layer = WORKSPACE_PATH_TO_LAYER[file.path];

    if (layer === "org") {
      return (
        <RulesFileEditor
          title="Your organization's rules"
          affects="Applies to every project and every person."
          kinds={ORG_KINDS}
          hints={ORG_KIND_HINTS}
          idPrefix="org"
          rules={org.data?.rules ?? []}
          isLoading={org.isLoading}
          loadError={org.error}
          loadErrorFallback="Could not load the organization rules"
          writeError={createOrgRule.error ?? retireOrgRule.error}
          restriction={orgReason}
          isAdding={createOrgRule.isPending}
          onAdd={addOrgRule}
          onRetire={(ruleId) => retireOrgRule.mutate(ruleId)}
          busyRuleId={retireOrgRule.isPending ? retireOrgRule.variables : undefined}
          emptyNote="Nothing here yet. House rules that hold no matter which client or project someone is asking about go here."
        />
      );
    }

    if (layer === "project") {
      return (
        <RulesFileEditor
          title="This project's rules"
          affects={`Applies to everyone working on ${selectedProject}.`}
          kinds={PROJECT_KINDS}
          hints={PROJECT_KIND_HINTS}
          idPrefix="project"
          rules={projectActive.data?.rules ?? []}
          proposed={projectProposed.data?.rules ?? []}
          isLoading={projectActive.isLoading || projectProposed.isLoading}
          loadError={projectActive.error ?? projectProposed.error}
          loadErrorFallback="Could not load this project's rules"
          writeError={
            createProjectRule.error ??
            retireProjectRule.error ??
            approveProjectRule.error
          }
          restriction={projectReason}
          isAdding={createProjectRule.isPending}
          onAdd={addProjectRule}
          onRetire={(ruleId) => retireProjectRule.mutate(ruleId)}
          onApprove={(ruleId) => approveProjectRule.mutate(ruleId)}
          busyRuleId={
            retireProjectRule.isPending
              ? retireProjectRule.variables
              : approveProjectRule.isPending
                ? approveProjectRule.variables
                : undefined
          }
          emptyNote="Nothing here yet. What is true about this client's landscape, and what its words mean, goes here."
        />
      );
    }

    if (layer === "person") {
      return (
        <section
          data-testid="guidance-pane-editor"
          className="space-y-3 border-t border-border px-3 py-3"
        >
          <div>
            <h4 className="text-sm font-semibold text-foreground">My preferences</h4>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Only changes how answers come to you. Nobody else working on this
              project sees these, and nobody else&apos;s answers change.
            </p>
          </div>
          <PreferencesEditor projectId={selectedProject} />
        </section>
      );
    }

    if (file.origin === "assembled") {
      return <AssembledNotes conflicts={preview.data?.conflicts ?? []} />;
    }

    if (file.origin === "manifest") {
      return (
        <section
          data-testid="guidance-pane-material"
          className="space-y-2 border-t border-border px-3 py-3"
        >
          <div>
            <h4 className="text-sm font-semibold text-foreground">
              What you uploaded, and what each file became
            </h4>
            <p className="mt-0.5 text-xs text-muted-foreground">
              The role you picked at upload decides what the assistant does with
              a file. To change one, upload it again under a different role.
            </p>
          </div>
          <ProjectMaterialSection projectId={selectedProject} />
        </section>
      );
    }

    return null;
  }

  return (
    <section aria-labelledby="project-structure-heading" className="space-y-4">
      <div>
        <h2 id="project-structure-heading" className="flex items-center gap-2 text-lg font-medium">
          <FolderTree className="h-5 w-5 text-muted-foreground" />
          What the assistant is told
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Before it answers anything, the assistant reads a set of instructions:
          your organization&apos;s house rules, what is true about this project,
          and how you personally want answers written. Each of those is a file
          below. Open one to read it, and to change it.
        </p>
      </div>

      <ProjectPicker value={selectedProject} onChange={setSelectedProject} />

      {selectedProject ? (
        <WorkspaceTree
          workspace={listed.workspace}
          pendingPaths={listed.pending}
          isLoading={workspace.isLoading}
          error={workspace.error}
          fileBudget={paneBudget}
          fileActions={(file) => (
            <FileOriginNote file={file} isPending={listed.pending.has(file.path)} />
          )}
          fileEditor={paneEditor}
        />
      ) : (
        <div
          data-testid="select-project-notice"
          className="rounded-lg border border-dashed border-border bg-muted/30 px-3 py-3 text-sm"
        >
          <p className="font-medium text-foreground">
            Select a project to see what the assistant is told about it.
          </p>
          <p className="mt-1 text-muted-foreground">
            Choosing one lists the files it reads &mdash; your organization&apos;s
            rules, this project&apos;s rules, your own answer preferences and the
            material you uploaded &mdash; and lets you change them where you open
            them.
          </p>
        </div>
      )}
    </section>
  );
}

// -- building blocks ---------------------------------------------------------

interface ProjectPickerProps {
  value: string;
  onChange: (projectId: string) => void;
}

/**
 * Which project everything below is about.
 *
 * Reads and writes the same selection the upload panel uses, so the two can
 * never show different projects. It is repeated here rather than referenced
 * ("choose one above") because a panel that renders nothing until you find a
 * control in another panel reads as a broken panel, not as an instruction.
 */
function ProjectPicker({ value, onChange }: ProjectPickerProps) {
  const { data, isLoading, error } = useDocsProjects();

  return (
    <div className="rounded-lg border border-border bg-card px-3 py-2.5">
      <label htmlFor="guidance-project" className="text-sm font-medium text-foreground">
        Project
      </label>
      <p className="mb-1.5 text-xs text-muted-foreground">
        Everything below is about this one project. Changing it here changes it
        in the upload panel too.
      </p>
      <div className="relative inline-block w-full max-w-sm">
        <select
          id="guidance-project"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          disabled={isLoading}
          className={cn(
            "w-full cursor-pointer appearance-none rounded-md border border-input bg-background",
            "py-1.5 pl-3 pr-8 text-sm",
            "focus:outline-none focus:ring-1 focus:ring-ring",
            "disabled:cursor-not-allowed disabled:opacity-60",
          )}
        >
          <option value="">{isLoading ? "Loading projects..." : "Select a project"}</option>
          {data?.projects.map((project) => (
            <option key={project.id} value={project.id}>
              {project.display_name}
            </option>
          ))}
        </select>
        <ChevronDown
          className="pointer-events-none absolute right-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
          aria-hidden
        />
      </div>
      {error != null && (
        <p className="mt-1.5 text-xs text-destructive" role="alert">
          {guidanceErrorMessage(error, "Could not load your projects")}
        </p>
      )}
    </div>
  );
}

/**
 * How the file open in the pane is changed.
 *
 * Every file here is generated: none is edited in place, and an
 * editable-looking pane over a file the server rewrites on the next question
 * would be a lie. The levels are changed by the editor directly below this
 * note, which rewrites the file; the rest are changed somewhere else entirely,
 * or by nothing at all, and say which.
 */
function FileOriginNote({
  file,
  isPending,
}: {
  file: WorkspaceFile;
  isPending: boolean;
}) {
  const layer = WORKSPACE_PATH_TO_LAYER[file.path];
  if (layer) {
    return (
      <p className="text-[11px] text-muted-foreground">
        {isPending
          ? "Written for you as soon as there is something to put in it. Add the first entry below."
          : "Written for you. Change what is in it below, and this file is rewritten."}
      </p>
    );
  }

  if (file.origin === "manifest") {
    return (
      <p className="text-[11px] text-muted-foreground">
        Built from the material uploaded to this project. To change what it says,
        upload a file again under a different role.
      </p>
    );
  }

  if (workspaceOriginDocumentId(file.origin) !== null) {
    return (
      <p className="text-[11px] text-muted-foreground">
        This came from a file you uploaded. Upload it again under a different
        role to change what it does, or delete the document to remove it.
      </p>
    );
  }

  return (
    <p className="text-[11px] text-muted-foreground">
      Put together automatically before every answer. Nothing edits it directly.
    </p>
  );
}

/**
 * What the assembled file cannot say about itself.
 *
 * `AGENTS.md` is the merged text and is read directly above this, so there is
 * nothing here to edit. There is one thing worth adding: when two levels pull
 * in different directions the renderer knows, and a rule that was silently
 * dropped is behaviour nobody can explain. Everything else about this file --
 * that it only gathers the others -- the viewer already says.
 */
function AssembledNotes({ conflicts }: { conflicts: string[] }) {
  return (
    <section
      data-testid="guidance-pane-notes"
      className="space-y-2 border-t border-border px-3 py-3"
    >
      {conflicts.length > 0 && (
        <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200">
          <p className="flex items-center gap-1.5 font-medium">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden />
            Two of these instructions pull in different directions
          </p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {conflicts.map((conflict) => (
              <li key={conflict}>{conflict}</li>
            ))}
          </ul>
        </div>
      )}
      <p className="text-[11px] text-muted-foreground">
        This is your own view: it includes your personal preferences and nobody
        else&apos;s.
      </p>
    </section>
  );
}
