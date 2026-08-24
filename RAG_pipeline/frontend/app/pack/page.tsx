import { PackWorkbench } from "@/components/packs/PackWorkbench";

/**
 * The Packs page. One of two mount points for the workbench; the other is the
 * chat side panel (`ChatLayout`).
 *
 * `?project=` scopes the "Use for this project" action. Unknown parameters are
 * ignored rather than treated as an error -- links to this page are emitted by
 * the harness and by older bookmarks.
 */
export default async function PackPage({
  searchParams,
}: {
  searchParams: Promise<{ project?: string }>;
}) {
  const { project } = await searchParams;
  return (
    <main className="mx-auto max-w-5xl p-6">
      <PackWorkbench projectId={project ?? null} />
    </main>
  );
}
