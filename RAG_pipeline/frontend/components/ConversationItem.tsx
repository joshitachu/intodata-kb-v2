"use client";

import { MessageSquare, Trash2, MoreVertical } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ConversationResponse } from "@/types";
import { useState } from "react";
import { ConfirmDialog } from "@/components/ConfirmDialog";

interface ConversationItemProps {
  conversation: ConversationResponse;
  isActive: boolean;
  onClick: () => void;
  onDelete: () => void;
}

export function ConversationItem({
  conversation,
  isActive,
  onClick,
  onDelete,
}: ConversationItemProps) {
  const [showMenu, setShowMenu] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  const title =
    conversation.title || `Conversation ${conversation.id}`;
  const messageCount = conversation.message_count;

  return (
    <div
      className={cn(
        "group relative flex items-center gap-2 px-3 py-2 rounded-md cursor-pointer transition-colors",
        isActive
          ? "bg-secondary text-secondary-foreground"
          : "hover:bg-muted text-muted-foreground hover:text-foreground"
      )}
      onClick={onClick}
    >
      <MessageSquare className="h-4 w-4 shrink-0" />
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium truncate">{title}</p>
        <p className="text-xs text-muted-foreground">
          {messageCount} {messageCount === 1 ? "message" : "messages"}
        </p>
      </div>

      <div className="relative">
        <button
          onClick={(e) => {
            e.stopPropagation();
            setShowMenu(!showMenu);
          }}
          className={cn(
            "p-1 rounded opacity-0 group-hover:opacity-100 transition-opacity",
            "hover:bg-muted-foreground/10"
          )}
        >
          <MoreVertical className="h-4 w-4" />
        </button>

        {showMenu && (
          <>
            <div
              className="fixed inset-0 z-10"
              onClick={(e) => {
                e.stopPropagation();
                setShowMenu(false);
              }}
            />
            <div className="absolute right-0 top-full mt-1 z-20 bg-popover border rounded-md shadow-md py-1 min-w-[120px]">
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setShowMenu(false);
                  setShowDeleteConfirm(true);
                }}
                className="flex items-center gap-2 w-full px-3 py-2 text-sm text-destructive hover:bg-muted"
              >
                <Trash2 className="h-4 w-4" />
                Delete
              </button>
            </div>
          </>
        )}
      </div>
      <ConfirmDialog
        open={showDeleteConfirm}
        title="Delete Conversation"
        message={`Delete "${title}"? All messages will be permanently removed.`}
        confirmLabel="Delete"
        onConfirm={() => {
          setShowDeleteConfirm(false);
          onDelete();
        }}
        onCancel={() => setShowDeleteConfirm(false)}
      />
    </div>
  );
}
