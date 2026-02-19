"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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
  const res = await fetch("/api/conversations");
  if (!res.ok) {
    const text = await res.text();
    let message = "Failed to fetch conversations";
    try { message = JSON.parse(text).error || message; } catch {}
    throw new Error(message);
  }
  return res.json();
}

async function fetchConversation(
  id: number
): Promise<ConversationDetailResponse> {
  const res = await fetch(`/api/conversations/${id}`);
  if (!res.ok) {
    const text = await res.text();
    let message = "Failed to fetch conversation";
    try { message = JSON.parse(text).error || message; } catch {}
    throw new Error(message);
  }
  return res.json();
}

async function createConversation(
  request?: ConversationCreateRequest
): Promise<ConversationResponse> {
  const res = await fetch("/api/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request || {}),
  });
  if (!res.ok) {
    const text = await res.text();
    let message = "Failed to create conversation";
    try { message = JSON.parse(text).error || message; } catch {}
    throw new Error(message);
  }
  return res.json();
}

async function updateConversation({
  id,
  request,
}: {
  id: number;
  request: ConversationUpdateRequest;
}): Promise<ConversationResponse> {
  const res = await fetch(`/api/conversations/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  if (!res.ok) {
    const text = await res.text();
    let message = "Failed to update conversation";
    try { message = JSON.parse(text).error || message; } catch {}
    throw new Error(message);
  }
  return res.json();
}

async function deleteConversation(id: number): Promise<void> {
  const res = await fetch(`/api/conversations/${id}`, { method: "DELETE" });
  if (!res.ok && res.status !== 204) {
    const text = await res.text();
    let message = "Failed to delete conversation";
    try { message = JSON.parse(text).error || message; } catch {}
    throw new Error(message);
  }
}

async function sendMessage({
  conversationId,
  request,
}: {
  conversationId: number;
  request: ConversationMessageRequest;
}): Promise<MessageResponse> {
  const res = await fetch(`/api/conversations/${conversationId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  if (!res.ok) {
    const text = await res.text();
    let message = "Failed to send message";
    try { message = JSON.parse(text).error || message; } catch {}
    throw new Error(message);
  }
  return res.json();
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
