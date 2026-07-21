import { redirect } from "next/navigation";
import { packWorkspaceHref } from "@/lib/packWorkspace";

export default async function PackGovernancePage({ searchParams }: { searchParams: Promise<{ project?: string; pack?: string }> }) {
  const { project, pack } = await searchParams;
  redirect(packWorkspaceHref({ projectId: project, packId: pack }));
}
