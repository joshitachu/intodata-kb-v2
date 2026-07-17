import { PackManager } from "@/components/harness-packs/PackManager";

export default async function PackGovernancePage({ searchParams }: { searchParams: Promise<{ project?: string; pack?: string }> }) {
  const { project, pack } = await searchParams;
  return <PackManager initialProjectId={project} initialPackName={pack} />;
}
