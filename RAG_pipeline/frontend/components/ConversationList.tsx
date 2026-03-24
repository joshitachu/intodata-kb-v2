"use client";

import { Plus, Loader2, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { cn } from "@/lib/utils";
import { ConversationItem } from "./ConversationItem";
import type { ConversationResponse } from "@/types";

interface ConversationListProps {
  conversations: ConversationResponse[];
  activeId: number | null;
  isLoading: boolean;
  onSelect: (id: number) => void;
  onCreate: () => void;
  onDelete: (id: number) => void;
  isCreating: boolean;
  isCollapsed: boolean;
  onToggleCollapse: () => void;
}

export function ConversationList({
  conversations,
  activeId,
  isLoading,
  onSelect,
  onCreate,
  onDelete,
  isCreating,
  isCollapsed,
  onToggleCollapse,
}: ConversationListProps) {
  if (isCollapsed) {
    return (
      <div className="w-12 border-r bg-card flex flex-col h-full items-center py-3 gap-2">
        <button
          onClick={onToggleCollapse}
          className="p-1.5 rounded-md hover:bg-muted transition-colors"
          title="Expand conversations"
        >
          <PanelLeftOpen className="h-4 w-4" />
        </button>
        <button
          onClick={onCreate}
          disabled={isCreating}
          className={cn(
            "p-1.5 rounded-md hover:bg-muted transition-colors",
            "disabled:opacity-50 disabled:cursor-not-allowed"
          )}
          title="New conversation"
        >
          {isCreating ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Plus className="h-4 w-4" />
          )}
        </button>
      </div>
    );
  }

  return (
    <div className="w-64 border-r bg-card flex flex-col h-full">
      <div className="p-4 border-b flex items-center justify-between">
        <h2 className="font-semibold text-sm">Conversations</h2>
        <div className="flex items-center gap-1">
          <button
            onClick={onCreate}
            disabled={isCreating}
            className={cn(
              "p-1.5 rounded-md hover:bg-muted transition-colors",
              "disabled:opacity-50 disabled:cursor-not-allowed"
            )}
            title="New conversation"
          >
            {isCreating ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Plus className="h-4 w-4" />
            )}
          </button>
          <button
            onClick={onToggleCollapse}
            className="p-1.5 rounded-md hover:bg-muted transition-colors"
            title="Collapse conversations"
          >
            <PanelLeftClose className="h-4 w-4" />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-2 space-y-1">
        {isLoading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : conversations.length === 0 ? (
          <div className="text-center py-8 text-sm text-muted-foreground">
            <p>No conversations yet.</p>
            <p className="mt-1">Click + to start one.</p>
          </div>
        ) : (
          conversations.map((conv) => (
            <ConversationItem
              key={conv.id}
              conversation={conv}
              isActive={conv.id === activeId}
              onClick={() => onSelect(conv.id)}
              onDelete={() => onDelete(conv.id)}
            />
          ))
        )}
      </div>
    </div>
  );
}
