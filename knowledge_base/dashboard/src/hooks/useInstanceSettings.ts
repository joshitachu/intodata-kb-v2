import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";

export const INSTANCE_SETTINGS_KEYS = [
  "rag_chat_model",
  "rag_embed_model",
  "pack_author_model",
  "doc_gen_analysis_model",
  "doc_gen_write_model",
] as const;

export type InstanceSettingKey = (typeof INSTANCE_SETTINGS_KEYS)[number];

export interface SettingSlot {
  key: InstanceSettingKey;
  label: string;
  description: string;
  endpoint: "mcp" | "rag";
}

export const SETTING_SLOTS: SettingSlot[] = [
  {
    key: "rag_chat_model",
    label: "RAG chat model",
    description: "LLM used for RAG chat answers and runtime tool calls.",
    endpoint: "rag",
  },
  {
    key: "rag_embed_model",
    label: "RAG embedding model",
    description: "Embedder used for document indexing and query embedding.",
    endpoint: "rag",
  },
  {
    key: "pack_author_model",
    label: "Pack author model",
    description: "Dedicated model used for one compact Pack design per conversational turn.",
    endpoint: "mcp",
  },
  {
    key: "doc_gen_analysis_model",
    label: "Doc gen analysis model",
    description: "LLM used for Phase 1 / Phase 2 analysis during doc generation.",
    endpoint: "mcp",
  },
  {
    key: "doc_gen_write_model",
    label: "Doc gen write model",
    description: "LLM used for Phase 3 prose writing during doc generation.",
    endpoint: "mcp",
  },
];

const STALE_MS = 30_000;

export function useInstanceSettings() {
  return useQuery({
    queryKey: ["instance-settings"],
    queryFn: api.getInstanceSettings,
    staleTime: STALE_MS,
    refetchOnWindowFocus: false,
  });
}

export function useUpdateInstanceSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (settings: Record<string, string | null>) =>
      api.updateInstanceSettings(settings),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["instance-settings"] });
    },
  });
}
