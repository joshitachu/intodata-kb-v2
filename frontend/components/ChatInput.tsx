"use client";

import { useState, useRef, useEffect, type FormEvent, type KeyboardEvent } from "react";
import { Send, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

interface ChatInputProps {
  onSend: (message: string) => void;
  isLoading: boolean;
  disabled?: boolean;
}

export function ChatInput({ onSend, isLoading, disabled }: ChatInputProps) {
  const [message, setMessage] = useState("");
  const pendingRef = useRef(false);

  useEffect(() => {
    if (!isLoading && pendingRef.current) {
      setMessage("");
      pendingRef.current = false;
    }
  }, [isLoading]);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = message.trim();
    if (trimmed.length > 0 && !isLoading && !disabled) {
      onSend(trimmed);
      pendingRef.current = true;
    }
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e as unknown as FormEvent);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="border-t p-4 bg-background">
      <div className="flex gap-2 items-end">
        <textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Type your message... (Shift+Enter for new line)"
          rows={1}
          className={cn(
            "flex-1 resize-none rounded-lg border border-input bg-background px-4 py-3",
            "text-sm placeholder:text-muted-foreground",
            "focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2",
            "disabled:cursor-not-allowed disabled:opacity-50",
            "min-h-[48px] max-h-[200px]"
          )}
          disabled={isLoading || disabled}
          style={{ height: "auto", overflow: "hidden" }}
          onInput={(e) => {
            const target = e.target as HTMLTextAreaElement;
            target.style.height = "auto";
            target.style.height = Math.min(target.scrollHeight, 200) + "px";
          }}
        />
        <button
          type="submit"
          disabled={isLoading || disabled || message.trim().length === 0}
          className={cn(
            "p-3 rounded-lg bg-primary text-primary-foreground",
            "hover:bg-primary/90 transition-colors",
            "disabled:opacity-50 disabled:cursor-not-allowed"
          )}
        >
          {isLoading ? (
            <Loader2 className="h-5 w-5 animate-spin" />
          ) : (
            <Send className="h-5 w-5" />
          )}
        </button>
      </div>
    </form>
  );
}
