import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";

// Nothing in this file polls. Every value here changes as a direct result of an
// operator action in this same UI -- connect, test, rotate, discover, bind --
// and each of those mutations invalidates the keys it affects. A background
// poll could only ever confirm what the mutation already told us.
/** Provider manifests compiled into the server; change on deploy. */
const CATALOGUE_STALE_MS = 5 * 60_000;
/** Rows an operator edits here; kept short so a second tab's edit shows up. */
const OPERATOR_EDITED_STALE_MS = 60_000;

// `enabled` so a modal can list the catalogue without every closed instance of
// that modal holding a live query.
export function useConnectorCatalog(enabled = true) {
  return useQuery({
    queryKey: ["connector-catalog"],
    queryFn: api.listConnectorCatalog,
    enabled,
    staleTime: CATALOGUE_STALE_MS,
  });
}

export function useConnectors(enabled = true) {
  return useQuery({
    queryKey: ["connectors"],
    queryFn: api.listConnectors,
    enabled,
    // Shorter than its siblings on purpose: `status` / `health_code` are the
    // result of a connection test, so this is the one row set an operator
    // watches for a change they did not make in this tab.
    staleTime: 15_000,
  });
}

export function useConnectorSources(connectorId: string | null, enabled = true) {
  return useQuery({
    queryKey: ["connector-sources", connectorId],
    queryFn: () => api.listConnectorSources(connectorId!),
    enabled: enabled && connectorId !== null,
    staleTime: OPERATOR_EDITED_STALE_MS,
  });
}

export function useProjectConnectorBindings(projectId: string | null, enabled = true) {
  return useQuery({
    queryKey: ["project-connector-bindings", projectId],
    queryFn: () => api.listProjectConnectorBindings(projectId!),
    enabled: enabled && projectId !== null,
    staleTime: OPERATOR_EDITED_STALE_MS,
  });
}

export function useProjectAvailableConnectors(projectId: string | null, enabled = true) {
  return useQuery({
    queryKey: ["project-available-connectors", projectId],
    queryFn: () => api.listProjectAvailableConnectors(projectId!),
    enabled: enabled && projectId !== null,
    staleTime: OPERATOR_EDITED_STALE_MS,
  });
}

/**
 * Invalidate everything derived from the connector instance/source tables.
 *
 * `GET /projects/{id}/available-connectors` is built from
 * `list_instances()` + `list_sources()`, so it changes on every connector and
 * source mutation -- but no mutation used to invalidate it. It only ever looked
 * correct because the removed global 5s poll refreshed it by accident, and it
 * is keyed per project so guessing which projects to touch is wrong: the whole
 * prefix goes.
 */
function invalidateConnectorDerived(queryClient: ReturnType<typeof useQueryClient>) {
  queryClient.invalidateQueries({ queryKey: ["connectors"] });
  queryClient.invalidateQueries({ queryKey: ["project-available-connectors"] });
}

export function useCreateConnector() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.createConnector,
    onSuccess: () => invalidateConnectorDerived(queryClient),
  });
}

export function useUpdateConnector() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ connectorId, data }: { connectorId: string; data: { display_name?: string; safe_config?: Record<string, unknown>; enabled?: boolean } }) =>
      api.updateConnector(connectorId, data),
    onSuccess: () => invalidateConnectorDerived(queryClient),
  });
}

export function useDeleteConnector() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.deleteConnector,
    onSuccess: () => invalidateConnectorDerived(queryClient),
  });
}

export function useTestConnector() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.testConnector,
    onSuccess: (_result, connectorId) => {
      invalidateConnectorDerived(queryClient);
      queryClient.invalidateQueries({ queryKey: ["connector-sources", connectorId] });
    },
  });
}

export function useRotateConnectorCredential() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ connectorId, credentials }: { connectorId: string; credentials: {
      credential: string;
      credential_type?: "api_key" | "basic";
      kibana_credential?: string;
      reuse_elasticsearch_credential?: boolean;
    } }) =>
      api.rotateConnectorCredential(connectorId, credentials),
    onSuccess: () => invalidateConnectorDerived(queryClient),
  });
}

export function useDiscoverConnectorSources() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: string | { connectorId: string; data?: { source_ids?: string[]; include_kibana?: boolean; kibana_space?: string; data_view_id?: string } }) =>
      typeof input === "string" ? api.discoverConnectorSources(input) : api.discoverConnectorSources(input.connectorId, input.data),
    onSuccess: (_result, input) => {
      const connectorId = typeof input === "string" ? input : input.connectorId;
      queryClient.invalidateQueries({ queryKey: ["connector-sources", connectorId] });
      // Discovery adds sources, which is exactly what the per-project
      // available-connectors view lists.
      invalidateConnectorDerived(queryClient);
    },
  });
}

// Form metadata, fetched from the remote Elastic/Kibana instance only while a
// dialog is open. Each call is a round trip to the customer's cluster.
export function useKibanaSpaces(connectorId: string | null, enabled = false) {
  return useQuery({ queryKey: ["kibana-spaces", connectorId], queryFn: () => api.listKibanaSpaces(connectorId!), enabled: enabled && connectorId !== null, staleTime: CATALOGUE_STALE_MS });
}

export function useKibanaDataViews(connectorId: string | null, space: string | null, enabled = false) {
  return useQuery({ queryKey: ["kibana-data-views", connectorId, space], queryFn: () => api.listKibanaDataViews(connectorId!, space!), enabled: enabled && connectorId !== null && space !== null, staleTime: CATALOGUE_STALE_MS });
}

export function usePutProjectConnectorBinding() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId, connectorId, ...data }: {
      projectId: string;
      connectorId: string;
      source_ids: string[];
      capability_ids: string[];
      budgets?: Record<string, number>;
      max_time_window_days?: number | null;
      enabled: boolean;
    }) => api.putProjectConnectorBinding(projectId, connectorId, data),
    onSuccess: (_result, variables) => {
      queryClient.invalidateQueries({ queryKey: ["project-connector-bindings", variables.projectId] });
      queryClient.invalidateQueries({ queryKey: ["connectors"] });
    },
  });
}

export function useDeleteProjectConnectorBinding() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ projectId, connectorId }: { projectId: string; connectorId: string }) =>
      api.deleteProjectConnectorBinding(projectId, connectorId),
    onSuccess: (_result, variables) => {
      queryClient.invalidateQueries({ queryKey: ["project-connector-bindings", variables.projectId] });
      queryClient.invalidateQueries({ queryKey: ["connectors"] });
    },
  });
}

/**
 * One run's connector/runtime evidence coverage.
 *
 * A published `_assessment/` artifact belonging to a finished run: immutable
 * once written, and only fetched while the disclosure is expanded.
 */
export function useRunConnectorEvidence(runId: number | null, enabled = false) {
  return useQuery({
    queryKey: ["run-connector-evidence", runId],
    queryFn: () => api.getRunConnectorEvidence(runId!),
    enabled: enabled && runId !== null,
    staleTime: CATALOGUE_STALE_MS,
  });
}
