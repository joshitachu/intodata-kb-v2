"use client";

import { FolderOpen } from "lucide-react";
import { SelectMenu, type SelectMenuOption } from "@/components/ui/select-menu";
import { SelectorFailed, SelectorPending } from "@/components/ui/selector-status";
import { queryOutcome } from "@/lib/queryState";
import { useUserProjects } from "@/hooks/useUserProjects";
import { useChatContext } from "@/contexts/ChatContext";

export function RagProjectSelector() {
  const { selectedProject, setSelectedProject } = useChatContext();
  const query = useUserProjects();
  const outcome = queryOutcome(query, "Could not load your projects");

  if (outcome.kind === "unknown") return <SelectorPending phase={outcome.phase} />;
  if (outcome.kind === "failed") {
    return <SelectorFailed message={outcome.message} onRetry={() => void query.refetch()} />;
  }

  // Nothing to scope by, so the control has nothing to offer. Reached only
  // from a confirmed empty response — never from an unanswered request, which
  // used to make the whole retrieval-scope control silently disappear.
  if (outcome.data.projects.length === 0) return null;

  const options: SelectMenuOption[] = [
    { value: "", label: "All Projects" },
    ...outcome.data.projects.map((project) => ({
      value: project.id,
      label: project.display_name,
    })),
  ];

  return (
    <div data-tour="project-selector">
      <SelectMenu
        value={selectedProject}
        onChange={setSelectedProject}
        options={options}
        placeholder="All Projects"
        icon={FolderOpen}
        ariaLabel="Retrieval project scope"
        className="max-w-[12rem]"
      />
    </div>
  );
}
