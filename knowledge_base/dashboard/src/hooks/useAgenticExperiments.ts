import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";

const EXPERIMENTS_KEY = "agentic-experiments";

export function useAgenticExperiments(projectId: string | null) {
  return useQuery({
    queryKey: [EXPERIMENTS_KEY, projectId],
    queryFn: () => api.listAgenticExperiments(projectId!),
    enabled: Boolean(projectId),
    refetchInterval: (query) => {
      const experiments = query.state.data ?? [];
      return experiments.some((experiment) =>
        ["reserved", "queued", "snapshotting", "running", "validating", "pending"].includes(experiment.status)
      )
        ? 5_000
        : false;
    },
  });
}

export function useAgenticExperimentCapabilities() {
  return useQuery({
    queryKey: [EXPERIMENTS_KEY, "capabilities"],
    queryFn: api.getAgenticExperimentCapabilities,
    staleTime: 60_000,
  });
}

export function useAgenticExperiment(experimentId: string | null) {
  return useQuery({
    queryKey: [EXPERIMENTS_KEY, "detail", experimentId],
    queryFn: () => api.getAgenticExperiment(experimentId!),
    enabled: Boolean(experimentId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && ["queued", "snapshotting", "running", "validating"].includes(status)
        ? 3_000
        : false;
    },
  });
}

export function useAgenticExperimentTasks(experimentId: string | null) {
  return useQuery({
    queryKey: [EXPERIMENTS_KEY, "tasks", experimentId],
    queryFn: () => api.listAgenticExperimentTasks(experimentId!),
    enabled: Boolean(experimentId),
    refetchInterval: (query) => {
      const tasks = query.state.data?.tasks ?? [];
      return tasks.some((task) => ["pending", "leased", "running"].includes(task.status)) ? 3_000 : false;
    },
  });
}

function invalidateExperimentQueries(queryClient: ReturnType<typeof useQueryClient>, projectId?: string) {
  void queryClient.invalidateQueries({ queryKey: [EXPERIMENTS_KEY] });
  if (projectId) void queryClient.invalidateQueries({ queryKey: [EXPERIMENTS_KEY, projectId] });
}

export function useCreateAgenticExperiment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Parameters<typeof api.createAgenticExperiment>[0]) => api.createAgenticExperiment(body),
    onSuccess: (experiment) => invalidateExperimentQueries(queryClient, experiment.project_id),
  });
}

export function useFreezeAgenticSourceSnapshot() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      experimentId,
      sources,
    }: {
      experimentId: string;
      sources: import("../types").AgenticSourceInput[];
    }) => api.freezeAgenticSourceSnapshot(experimentId, sources),
    onSuccess: () => invalidateExperimentQueries(queryClient),
  });
}

export function useReconcileAgenticExperiment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (experimentId: string) => api.reconcileAgenticExperiment(experimentId),
    onSuccess: () => invalidateExperimentQueries(queryClient),
  });
}

export function useAgenticExperimentRetention() {
  const queryClient = useQueryClient();
  const quarantine = useMutation({
    mutationFn: (experimentId: string) => api.quarantineAgenticExperiment(experimentId),
    onSuccess: () => invalidateExperimentQueries(queryClient),
  });
  const restore = useMutation({
    mutationFn: (experimentId: string) => api.restoreAgenticExperiment(experimentId),
    onSuccess: () => invalidateExperimentQueries(queryClient),
  });
  return { quarantine, restore };
}

export function useCompileAgenticRecipe() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ experimentId, recipe }: { experimentId: string; recipe: Parameters<typeof api.compileAgenticRecipe>[1] }) =>
      api.compileAgenticRecipe(experimentId, recipe),
    onSuccess: () => invalidateExperimentQueries(queryClient),
  });
}

export function useRetryAgenticExperimentTask() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ experimentId, taskRunId }: { experimentId: string; taskRunId: string }) =>
      api.retryAgenticExperimentTask(experimentId, taskRunId),
    onSuccess: (_task, variables) => {
      void queryClient.invalidateQueries({ queryKey: [EXPERIMENTS_KEY, "tasks", variables.experimentId] });
      invalidateExperimentQueries(queryClient);
    },
  });
}
