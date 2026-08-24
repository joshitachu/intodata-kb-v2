import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";

export function useConnectorCatalog() {
  return useQuery({
    queryKey: ["connector-catalog"],
    queryFn: api.listConnectorCatalog,
    staleTime: 60_000,
  });
}

export function useConnectors(enabled = true) {
  return useQuery({
    queryKey: ["connectors"],
    queryFn: api.listConnectors,
    enabled,
    staleTime: 15_000,
  });
}

export function useConnectorSources(connectorId: string | null, enabled = true) {
  return useQuery({
    queryKey: ["connector-sources", connectorId],
    queryFn: () => api.listConnectorSources(connectorId!),
    enabled: enabled && connectorId !== null,
  });
}

export function useProjectConnectorBindings(projectId: string | null, enabled = true) {
  return useQuery({
    queryKey: ["project-connector-bindings", projectId],
    queryFn: () => api.listProjectConnectorBindings(projectId!),
    enabled: enabled && projectId !== null,
  });
}

export function useProjectAvailableConnectors(projectId: string | null, enabled = true) {
  return useQuery({
    queryKey: ["project-available-connectors", projectId],
    queryFn: () => api.listProjectAvailableConnectors(projectId!),
    enabled: enabled && projectId !== null,
  });
}

export function useCreateConnector() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.createConnector,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["connectors"] }),
  });
}

export function useUpdateConnector() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ connectorId, data }: { connectorId: string; data: { display_name?: string; safe_config?: Record<string, unknown>; enabled?: boolean } }) =>
      api.updateConnector(connectorId, data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["connectors"] }),
  });
}

export function useDeleteConnector() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.deleteConnector,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["connectors"] }),
  });
}

export function useTestConnector() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: api.testConnector,
    onSuccess: (_result, connectorId) => {
      queryClient.invalidateQueries({ queryKey: ["connectors"] });
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
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["connectors"] }),
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
      queryClient.invalidateQueries({ queryKey: ["connectors"] });
    },
  });
}

export function useKibanaSpaces(connectorId: string | null, enabled = false) {
  return useQuery({ queryKey: ["kibana-spaces", connectorId], queryFn: () => api.listKibanaSpaces(connectorId!), enabled: enabled && connectorId !== null });
}

export function useKibanaDataViews(connectorId: string | null, space: string | null, enabled = false) {
  return useQuery({ queryKey: ["kibana-data-views", connectorId, space], queryFn: () => api.listKibanaDataViews(connectorId!, space!), enabled: enabled && connectorId !== null && space !== null });
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

export function useRunConnectorEvidence(runId: number | null, enabled = false) {
  return useQuery({
    queryKey: ["run-connector-evidence", runId],
    queryFn: () => api.getRunConnectorEvidence(runId!),
    enabled: enabled && runId !== null,
  });
}
