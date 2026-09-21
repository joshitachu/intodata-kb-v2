"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchJson } from "@/lib/authedFetch";
import type {
  ConversationCreateRequest,
  ConversationDetailResponse,
  ConversationListResponse,
  ConversationMessageRequest,
  ConversationResponse,
  ConversationUpdateRequest,
  MessageResponse,
} from "@/types";

// The chat lane this hook talks to. Production is `/api`; the experimental
// OpenCode lane at `/labs/chat` passes `/api/labs`. The lane is appended to the
// query key rather than prepended so that every existing
// `invalidateQueries({ queryKey: ["conversations"] })` and
// `["conversation", id]` call still prefix-matches both lanes.
const DEFAULT_API_BASE = "/api";

// --- Fetch functions ---

async function fetchConversations(
  apiBase: string
): Promise<ConversationListResponse> {
  return fetchJson<ConversationListResponse>(`${apiBase}/conversations`, {
    fallbackError: "Failed to fetch conversations",
  });
}

async function fetchConversation(
  id: number,
  apiBase: string
): Promise<ConversationDetailResponse> {
  return fetchJson<ConversationDetailResponse>(`${apiBase}/conversations/${id}`, {
    fallbackError: "Failed to fetch conversation",
  });
}

async function createConversation(
  request: ConversationCreateRequest | undefined,
  apiBase: string
): Promise<ConversationResponse> {
  return fetchJson<ConversationResponse>(`${apiBase}/conversations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request || {}),
    fallbackError: "Failed to create conversation",
  });
}

async function updateConversation(
  {
    id,
    request,
  }: {
    id: number;
    request: ConversationUpdateRequest;
  },
  apiBase: string
): Promise<ConversationResponse> {
  return fetchJson<ConversationResponse>(`${apiBase}/conversations/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    fallbackError: "Failed to update conversation",
  });
}

async function deleteConversation(id: number, apiBase: string): Promise<void> {
  // The route answers 204, which `fetchJson` returns without parsing a body.
  await fetchJson<unknown>(`${apiBase}/conversations/${id}`, {
    method: "DELETE",
    fallbackError: "Failed to delete conversation",
  });
}

async function sendMessage(
  {
    conversationId,
    request,
  }: {
    conversationId: number;
    request: ConversationMessageRequest;
  },
  apiBase: string
): Promise<MessageResponse> {
  return fetchJson<MessageResponse>(`${apiBase}/conversations/${conversationId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    fallbackError: "Failed to send message",
  });
}

// --- Hooks ---

export function useConversations(apiBase: string = DEFAULT_API_BASE) {
  return useQuery({
    queryKey: ["conversations", apiBase],
    queryFn: () => fetchConversations(apiBase),
  });
}

export function useConversation(
  id: number | null,
  apiBase: string = DEFAULT_API_BASE
) {
  return useQuery({
    queryKey: ["conversation", id, apiBase],
    queryFn: () => fetchConversation(id!, apiBase),
    enabled: id !== null,
  });
}

export function useCreateConversation(apiBase: string = DEFAULT_API_BASE) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request?: ConversationCreateRequest) =>
      createConversation(request, apiBase),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["conversations", apiBase] });
    },
  });
}

export function useUpdateConversation(apiBase: string = DEFAULT_API_BASE) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (variables: { id: number; request: ConversationUpdateRequest }) =>
      updateConversation(variables, apiBase),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["conversations", apiBase] });
      queryClient.invalidateQueries({ queryKey: ["conversation", data.id, apiBase] });
    },
  });
}

export function useDeleteConversation(apiBase: string = DEFAULT_API_BASE) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => deleteConversation(id, apiBase),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["conversations", apiBase] });
    },
  });
}

export function useSendMessage(
  conversationId: number,
  apiBase: string = DEFAULT_API_BASE
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: ConversationMessageRequest) =>
      sendMessage({ conversationId, request }, apiBase),
    onSuccess: () => {
      // Invalidate the specific conversation to refetch messages
      queryClient.invalidateQueries({
        queryKey: ["conversation", conversationId, apiBase],
      });
      // Also invalidate the list to update message counts
      queryClient.invalidateQueries({ queryKey: ["conversations", apiBase] });
    },
  });
}
