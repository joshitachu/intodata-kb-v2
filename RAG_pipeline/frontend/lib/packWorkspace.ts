export type PackWorkspaceTarget = {
  conversationId?: number | null;
  projectId?: string;
  packId?: string | number;
};

export const PACK_CREATE_EVENT = "pack-workbench:create";
const PACK_CREATE_STORAGE_KEY = "pack-workbench-create-command";

export function packWorkspaceHref({ conversationId, projectId, packId }: PackWorkspaceTarget = {}): string {
  const query = new URLSearchParams({ workspace: "pack" });
  if (conversationId) query.set("c", String(conversationId));
  if (projectId) query.set("project", projectId);
  if (packId !== undefined && packId !== "") query.set("pack", String(packId));
  return `/?${query.toString()}`;
}

export function closePackWorkspaceHref(search: URLSearchParams): string {
  const next = new URLSearchParams(search);
  next.delete("workspace");
  next.delete("project");
  next.delete("pack");
  const value = next.toString();
  return value ? `/?${value}` : "/";
}

export function stagePackCreateCommand(command: string): void {
  if (typeof window === "undefined") return;
  window.sessionStorage.setItem(PACK_CREATE_STORAGE_KEY, command);
  window.dispatchEvent(new Event(PACK_CREATE_EVENT));
}

export function takePackCreateCommand(): string | null {
  if (typeof window === "undefined") return null;
  const command = window.sessionStorage.getItem(PACK_CREATE_STORAGE_KEY);
  window.sessionStorage.removeItem(PACK_CREATE_STORAGE_KEY);
  return command;
}
