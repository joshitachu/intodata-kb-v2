"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Plus, Loader2, MessageSquare, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { useChatContextSafe } from "@/contexts/ChatContext";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { LaneBadge } from "@/components/chat/LaneBadge";
import { LaneFilterControl } from "@/components/chat/LaneFilterControl";
import { LANE_FILTER_LABEL } from "@/components/chat/lane";
import type { ConversationResponse } from "@/types";

export function MobileChatList() {
  const chat = useChatContextSafe();
  const router = useRouter();
  const [deleteTarget, setDeleteTarget] = useState<ConversationResponse | null>(null);

  // Same lane filter as the desktop sidebar, over the same provider state, so
  // narrowing on one surface is not silently undone by the other.
  const sorted = useMemo(() => {
    if (!chat) return [];
    return [...chat.visibleConversations].sort((a, b) => {
      const at = a.updated_at ?? a.created_at;
      const bt = b.updated_at ?? b.created_at;
      return bt.localeCompare(at);
    });
  }, [chat?.visibleConversations]);
  const hasHiddenByFilter =
    chat !== null &&
    chat.laneFilter !== "all" &&
    chat.conversations.length > sorted.length;

  function open(id: number) {
    router.push(`/?c=${id}`);
  }

  function startNew() {
    router.push("/?c=new");
  }

  if (!chat) {
    return (
      <div className="flex flex-1 items-center justify-center text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" />
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col">
      <div className="p-3 border-b border-border/60">
        <button
          onClick={startNew}
          className="w-full inline-flex items-center justify-center gap-2 rounded-xl bg-primary px-4 py-3 text-sm font-medium text-primary-foreground hover:bg-primary/90 active:scale-[0.99] transition-all min-h-[48px] shadow-elevation-xs"
        >
          <Plus className="h-4 w-4" />
          New Chat
        </button>
        <LaneFilterControl
          value={chat.laneFilter}
          onChange={chat.setLaneFilter}
          className="mt-2 flex w-full"
        />
      </div>

      <div className="flex-1 overflow-y-auto">
        {chat.isLoading ? (
          <div className="flex items-center justify-center py-10 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin" />
          </div>
        ) : sorted.length === 0 ? (
          <div className="px-6 py-12 text-center text-sm text-muted-foreground">
            <MessageSquare className="h-10 w-10 mx-auto mb-3 opacity-40" />
            {hasHiddenByFilter ? (
              <>
                <p>No {LANE_FILTER_LABEL[chat.laneFilter]} conversations.</p>
                <p className="mt-1 text-xs">
                  Other lanes are hidden by the filter above.
                </p>
              </>
            ) : (
              <>
                <p>No conversations yet.</p>
                <p className="mt-1 text-xs">Tap "New Chat" to start one.</p>
              </>
            )}
          </div>
        ) : (
          <ul className="divide-y divide-border/60">
            {sorted.map((conv) => (
              <li key={conv.id}>
                <div className="group flex items-center gap-2 px-4 py-3 min-h-[56px] hover:bg-muted/50 active:bg-muted transition-colors">
                  <button
                    onClick={() => open(conv.id)}
                    className="flex-1 min-w-0 text-left"
                  >
                    <div className="text-sm font-medium truncate text-foreground">
                      {conv.title || "New Chat"}
                    </div>
                    <div className="mt-0.5 flex items-center gap-1.5 text-xs text-muted-foreground">
                      <LaneBadge lane={conv.lane} />
                      <span className="truncate">
                        {formatDate(conv.updated_at ?? conv.created_at)}
                      </span>
                    </div>
                  </button>
                  <button
                    onClick={() => setDeleteTarget(conv)}
                    aria-label={`Delete conversation ${conv.title || "New Chat"}`}
                    className="shrink-0 p-2 rounded-md text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors min-h-[40px] min-w-[40px] flex items-center justify-center"
                    title="Delete"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete Conversation"
        message={`Delete "${deleteTarget?.title || "New Chat"}"? All messages will be permanently removed.`}
        confirmLabel="Delete"
        onConfirm={() => {
          if (deleteTarget && chat) {
            if (chat.activeConversationId === deleteTarget.id) {
              router.replace("/?c=new");
            }
            chat.deleteConversation(deleteTarget.id);
          }
          setDeleteTarget(null);
        }}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}

function formatDate(iso: string | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  if (sameDay) {
    return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  }
  const sameYear = d.getFullYear() === now.getFullYear();
  return d.toLocaleDateString(undefined, sameYear
    ? { month: "short", day: "numeric" }
    : { year: "numeric", month: "short", day: "numeric" });
}
