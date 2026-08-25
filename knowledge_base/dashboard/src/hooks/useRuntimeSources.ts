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
