"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchJson } from "@/lib/authedFetch";
import {
  NotificationList,
  NotificationListSchema,
  UnreadCount,
  UnreadCountSchema,
} from "@/types/onboarding";

async function fetchList(): Promise<NotificationList> {
  return NotificationListSchema.parse(
    await fetchJson<unknown>("/api/notifications", {
      cache: "no-store",
      fallbackError: "Failed to fetch notifications",
    }),
  );
}

async function fetchUnreadCount(): Promise<UnreadCount> {
  return UnreadCountSchema.parse(
    await fetchJson<unknown>("/api/notifications/unread-count", {
      cache: "no-store",
      fallbackError: "Failed to fetch unread count",
    }),
  );
}

async function markRead(id: number): Promise<void> {
  await fetchJson<unknown>(`/api/notifications/${id}/read`, {
    method: "PUT",
    fallbackError: "Failed to mark as read",
  });
}

async function markAllRead(): Promise<void> {
  await fetchJson<unknown>("/api/notifications/read-all", {
    method: "PUT",
    fallbackError: "Failed to mark all as read",
  });
}

export function useNotifications() {
  return useQuery({
    queryKey: ["notifications", "list"],
    queryFn: fetchList,
    staleTime: 15 * 1000,
  });
}

export function useUnreadCount(enabled: boolean = true) {
  return useQuery({
    queryKey: ["notifications", "unread-count"],
    queryFn: fetchUnreadCount,
    enabled,
    refetchInterval: enabled ? 30 * 1000 : false,
    staleTime: 10 * 1000,
  });
}

export function useMarkRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: markRead,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["notifications"] });
    },
  });
}

export function useMarkAllRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: markAllRead,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["notifications"] });
    },
  });
}
