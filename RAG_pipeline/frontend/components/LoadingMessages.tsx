"use client";

import { useState, useEffect } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

const MESSAGES = [
  "Searching knowledge base...",
  "Retrieving relevant documents...",
  "Analyzing context...",
  "Generating response...",
];

const CYCLE_MS = 2500;

interface LoadingMessagesProps {
  className?: string;
}

export function LoadingMessages({ className }: LoadingMessagesProps) {
  const [index, setIndex] = useState(0);
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    const interval = setInterval(() => {
      setVisible(false);
      setTimeout(() => {
        setIndex((prev) => (prev + 1) % MESSAGES.length);
        setVisible(true);
      }, 300);
    }, CYCLE_MS);

    return () => clearInterval(interval);
  }, []);

  return (
    <div className={cn("flex items-center gap-3", className)}>
      <Loader2 className="h-4 w-4 animate-spin text-muted-foreground shrink-0" />
      <span
        className={cn(
          "text-sm text-muted-foreground transition-opacity duration-300",
          visible ? "opacity-100" : "opacity-0"
        )}
      >
        {MESSAGES[index]}
      </span>
    </div>
  );
}
