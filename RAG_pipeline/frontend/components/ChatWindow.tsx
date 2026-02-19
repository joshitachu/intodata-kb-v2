"use client";

import { Bot, Loader2 } from "lucide-react";
import { MessageList } from "./MessageList";
import { ChatInput } from "./ChatInput";
import type { ConversationDetailResponse, ConversationMessageRequest } from "@/types";
import type { StreamState } from "@/hooks/useStreamMessage";

interface ChatWindowProps {
  conversation: ConversationDetailResponse | null;
  isLoading: boolean;
  isSending: boolean;
  onSendMessage: (request: ConversationMessageRequest) => void;
  streamingMessage?: StreamState | null;
  pendingUserMessage?: string | null;
}

export function ChatWindow({
  conversation,
  isLoading,
  isSending,
  onSendMessage,
  streamingMessage,
  pendingUserMessage,
}: ChatWindowProps) {
  if (!conversation) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-muted-foreground">
        <p>Select a conversation or create a new one to start chatting.</p>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  function handleSend(message: string) {
    onSendMessage({ query: message });
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="border-b p-4">
        <h2 className="font-semibold">
          {conversation.title || `Conversation ${conversation.id}`}
        </h2>
      </div>
      <div className="flex-1 overflow-y-auto">
        <MessageList
          messages={conversation.messages}
          streamingMessage={streamingMessage}
          pendingUserMessage={pendingUserMessage}
        />
      </div>
      <ChatInput
        onSend={handleSend}
        isLoading={isSending}
        disabled={false}
      />
    </div>
  );
}
