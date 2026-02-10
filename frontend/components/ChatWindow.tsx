"use client";

import { Bot, Loader2 } from "lucide-react";
import { MessageList } from "./MessageList";
import { ChatInput } from "./ChatInput";
import { LoadingMessages } from "./LoadingMessages";
import type { ConversationDetailResponse, ConversationMessageRequest } from "@/types";

interface ChatWindowProps {
  conversation: ConversationDetailResponse | null;
  isLoading: boolean;
  isSending: boolean;
  onSendMessage: (request: ConversationMessageRequest) => void;
}

export function ChatWindow({
  conversation,
  isLoading,
  isSending,
  onSendMessage,
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
        <MessageList messages={conversation.messages} />
        {isSending && (
          <div className="flex gap-3 p-4">
            <div className="flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center bg-muted">
              <Bot className="h-4 w-4" />
            </div>
            <div className="flex items-center">
              <LoadingMessages />
            </div>
          </div>
        )}
      </div>
      <ChatInput
        onSend={handleSend}
        isLoading={isSending}
        disabled={false}
      />
    </div>
  );
}
