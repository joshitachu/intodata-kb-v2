"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  NotificationList,
  NotificationListSchema,
  UnreadCount,
  UnreadCountSchema,
} from "@/types/onboarding";

async function fetchList(): Promise<NotificationList> {
  const res = await fetch("/api/notifications", { cache: "no-store" });
  if (!res.ok) {
    throw new Error("Failed to fetch notifications");
  }
  return NotificationListSchema.parse(await res.json());
}

async function fetchUnreadCount(): Promise<UnreadCount> {
  const res = await fetch("/api/notifications/unread-count", {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error("Failed to fetch unread count");
  }
  return UnreadCountSchema.parse(await res.json());
}

async function markRead(id: number): Promise<void> {
  const res = await fetch(`/api/notifications/${id}/read`, { method: "PUT" });
  if (!res.ok) {
    throw new Error("Failed to mark as read");
  }
}

async function markAllRead(): Promise<void> {
  const res = await fetch("/api/notifications/read-all", { method: "PUT" });
  if (!res.ok) {
    throw new Error("Failed to mark all as read");
  }
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
