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

// --- Fetch functions ---

async function fetchConversations(): Promise<ConversationListResponse> {
  return fetchJson<ConversationListResponse>("/api/conversations", {
    fallbackError: "Failed to fetch conversations",
  });
}

async function fetchConversation(
  id: number
): Promise<ConversationDetailResponse> {
  return fetchJson<ConversationDetailResponse>(`/api/conversations/${id}`, {
    fallbackError: "Failed to fetch conversation",
  });
}

async function createConversation(
  request?: ConversationCreateRequest
): Promise<ConversationResponse> {
  return fetchJson<ConversationResponse>("/api/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request || {}),
    fallbackError: "Failed to create conversation",
  });
}

async function updateConversation({
  id,
  request,
}: {
  id: number;
  request: ConversationUpdateRequest;
}): Promise<ConversationResponse> {
  return fetchJson<ConversationResponse>(`/api/conversations/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    fallbackError: "Failed to update conversation",
  });
}

async function deleteConversation(id: number): Promise<void> {
  // The route answers 204, which `fetchJson` returns without parsing a body.
  await fetchJson<unknown>(`/api/conversations/${id}`, {
    method: "DELETE",
    fallbackError: "Failed to delete conversation",
  });
}

async function sendMessage({
  conversationId,
  request,
}: {
  conversationId: number;
  request: ConversationMessageRequest;
}): Promise<MessageResponse> {
  return fetchJson<MessageResponse>(`/api/conversations/${conversationId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    fallbackError: "Failed to send message",
  });
}

// --- Hooks ---

export function useConversations() {
  return useQuery({
    queryKey: ["conversations"],
    queryFn: fetchConversations,
  });
}

export function useConversation(id: number | null) {
  return useQuery({
    queryKey: ["conversation", id],
    queryFn: () => fetchConversation(id!),
    enabled: id !== null,
  });
}

export function useCreateConversation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createConversation,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
}

export function useUpdateConversation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: updateConversation,
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      queryClient.invalidateQueries({ queryKey: ["conversation", data.id] });
    },
  });
}

export function useDeleteConversation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteConversation,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
}

export function useSendMessage(conversationId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: ConversationMessageRequest) =>
      sendMessage({ conversationId, request }),
    onSuccess: () => {
      // Invalidate the specific conversation to refetch messages
      queryClient.invalidateQueries({
        queryKey: ["conversation", conversationId],
      });
      // Also invalidate the list to update message counts
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
}
