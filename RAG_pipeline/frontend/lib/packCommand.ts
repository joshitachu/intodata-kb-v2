import type { UserProject } from "@/types";

export type PackCommandResolution =
  | { href: string }
  | { message: string }
  | { error: string };

export function resolvePackCommand(
  argumentsText: string,
  projects: UserProject[],
  contextualProjectId?: string,
): PackCommandResolution {
  const value = argumentsText.trim();
  const action = value.split(/\s+/, 1)[0]?.toLowerCase();

  if (!action || action === "list") return { href: "/pack" };

  if (action === "create") {
    return { message: `/pack ${value}` };
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
    const query = new URLSearchParams({ project: project.id, pack: requestedPack, mode: "stack" });
    return { href: `/pack?${query.toString()}` };
  }

  return { error: `Unknown Pack command “${action}”. Use list, create, or use.` };
}
