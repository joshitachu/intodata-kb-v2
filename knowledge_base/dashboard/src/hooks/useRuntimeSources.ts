import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";

/**
 * Runtime sources for one connector provider.
 *
 * Separate from `useConnectors` because these rows live in a different store:
 * the RAG backend's `runtime_sources` tables, reached through the MCP server's
 * proxy. A connector row for a runtime-managed provider needs BOTH -- the
 * manifest from the catalogue, the coverage from here.
 */
export function useRuntimeSources(
  providerId: string | null,
  enabled = true,
  // Optional PROJECT narrowing, served by the same proxy route. The Connectors
  // directory wants every project a provider mirrors; the Generate dialog wants
  // the answer to "which source will THIS run read", which is a different
  // question with a different answer -- a provider can be connected to Nexus
  // and still have nothing registered for the project being generated.
  projectId?: string | null,
) {
  return useQuery({
    queryKey: ["runtime-sources", providerId, projectId ?? null],
    queryFn: () =>
      api.listRuntimeSources({
        providerId: providerId ?? undefined,
        projectId: projectId ?? undefined,
      }),
    enabled: enabled && providerId !== null,
    staleTime: 15_000,
  });
}

/**
 * The read scope a documentation run for this project will apply.
 *
 * Separate from the source list because it is a different question: the list
 * says WHICH mirror is registered, this says WHAT SLICE of it a run reads.
 * A source can be healthy and fully synced while the run reads one environment
 * of four, and only this answers that.
 */
export function useRuntimeReadScope(projectId: string | null, enabled = true) {
  return useQuery({
    queryKey: ["runtime-read-scope", projectId],
    queryFn: () => api.getRuntimeReadScope(projectId as string),
    enabled: enabled && Boolean(projectId),
    // Best-effort: a caller who cannot read it sees the row without the scope
    // line rather than an error where the source list should be.
    retry: false,
    staleTime: 15_000,
  });
}

/**
 * Credential-form schema per runtime provider, served from the provider itself.
 *
 * Fetched rather than hardcoded so the connect form always matches
 * `credential_schema()` and cannot drift into offering a field the provider does
 * not have. Only requested while a dialog is open -- it is form metadata, not
 * page furniture.
 */
export function useRuntimeSourceTypes(enabled = false) {
  return useQuery({
    queryKey: ["runtime-source-types"],
    queryFn: api.listRuntimeSourceTypes,
    enabled,
    staleTime: 5 * 60_000,
  });
}

function invalidateRuntimeSources(queryClient: ReturnType<typeof useQueryClient>) {
  // Keyed on the provider, so invalidate the prefix rather than guessing which
  // provider's list a mutation touched.
  queryClient.invalidateQueries({ queryKey: ["runtime-sources"] });
}

export function useCreateRuntimeSource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.createRuntimeSource,
    onSuccess: () => invalidateRuntimeSources(queryClient),
  });
}

export function useSyncRuntimeSource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.syncRuntimeSource,
    onSuccess: () => invalidateRuntimeSources(queryClient),
  });
}

export function useDeleteRuntimeSource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.deleteRuntimeSource,
    onSuccess: () => invalidateRuntimeSources(queryClient),
  });
}

/**
 * Projects attached to one source.
 *
 * These rows ARE the source-to-project relationship: a source has no owning
 * project, only attachments, and any number of them. Also the supported answer
 * to "That physical runtime source is already registered": one Talend tenant is
 * polled ONCE, and every project documenting that estate attaches to it.
 * Registering the credential again is refused on purpose -- it would double the
 * vendor poll and leave two mirrors of one estate free to drift apart.
 */
export function useRuntimeSourceReaders(sourceId: number | null, enabled = true) {
  return useQuery({
    queryKey: ["runtime-source-readers", sourceId],
    queryFn: () => api.listRuntimeSourceReaders(sourceId as number),
    enabled: enabled && sourceId !== null,
    // Best-effort, like the read scope: a caller without permission sees the
    // source row without its attachment list, not an error in place of it.
    retry: false,
    staleTime: 15_000,
  });
}

function invalidateReaders(
  queryClient: ReturnType<typeof useQueryClient>,
  sourceId: number,
) {
  queryClient.invalidateQueries({ queryKey: ["runtime-source-readers", sourceId] });
  // An attachment changes what a documentation run for the attached project
  // reads, so the scope line for every project is now potentially stale.
  queryClient.invalidateQueries({ queryKey: ["runtime-read-scope"] });
  // It also changes `linked_project_ids` on the source row itself, which is
  // what the connector row renders at rest.
  invalidateRuntimeSources(queryClient);
}

export function useAddRuntimeSourceReader(sourceId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: {
      project_id: string;
      environment_ids?: string[] | null;
      window_days?: number | null;
    }) => api.addRuntimeSourceReader(sourceId, data),
    onSuccess: () => invalidateReaders(queryClient, sourceId),
  });
}

export function useRemoveRuntimeSourceReader(sourceId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (projectId: string) => api.removeRuntimeSourceReader(sourceId, projectId),
    onSuccess: () => invalidateReaders(queryClient, sourceId),
  });
}
