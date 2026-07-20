import type { UserProject } from "@/types";
import { packWorkspaceHref } from "@/lib/packWorkspace";

export type PackCommandResolution =
  | { href: string; message?: string }
  | { error: string };

export function resolvePackCommand(
  argumentsText: string,
  projects: UserProject[],
  contextualProjectId?: string,
  conversationId?: number,
): PackCommandResolution {
  const value = argumentsText.trim();
  const action = value.split(/\s+/, 1)[0]?.toLowerCase();

  if (!action || action === "list") {
    return { href: packWorkspaceHref({ conversationId, projectId: contextualProjectId }) };
  }

  if (action === "create") {
    return {
      href: packWorkspaceHref({ conversationId, projectId: contextualProjectId, packId: "create" }),
      message: `/pack ${value}`,
    };
  }

  if (action === "use") {
    const match = value.match(/^use\s+(.+?)\s+for\s+(.+)$/i);
    if (!match) return { error: "Use /pack use <Pack name> for <project>." };
    const requestedPack = match[1].trim().replace(/^["']|["']$/g, "");
    const projectQuery = match[2].trim().replace(/^["']|["']$/g, "").toLowerCase();
    const project = projects.find((item) => (
      item.id.toLowerCase() === projectQuery || item.display_name.toLowerCase() === projectQuery
    ));
    if (!project) return { error: `Project “${match[2].trim()}” is not available in your current scope.` };
    return { href: packWorkspaceHref({ conversationId, projectId: project.id, packId: requestedPack }) };
  }

  return { error: `Unknown Pack command “${action}”. Use list, create, or use.` };
}
