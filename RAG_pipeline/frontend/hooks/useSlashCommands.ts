"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { authedFetch } from "@/lib/authedFetch";
import { SlashCommandListResponseSchema, type SlashCommand } from "@/types";

export function filterSlashCommands(commands: SlashCommand[], query: string) {
  const normalized = query.trim().replace(/^\//, "").toLowerCase();
  if (!normalized) return commands;
  return commands.filter((command) =>
    [command.name, command.description, command.argument_hint ?? ""]
      .join(" ")
      .toLowerCase()
      .includes(normalized),
  );
}

/** Fetches command discovery metadata for input surfaces such as ChatInput. */
export function useSlashCommands() {
  const [commands, setCommands] = useState<SlashCommand[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    // `authedFetch` forwards `init` untouched, so the abort signal still
    // cancels both the first attempt and a post-refresh replay.
    void authedFetch("/api/conversations/commands", { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("Command discovery is unavailable");
        return SlashCommandListResponseSchema.parse(await response.json());
      })
      .then((payload) => setCommands(payload.commands))
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setError(reason instanceof Error ? reason.message : "Command discovery is unavailable");
      });
    return () => controller.abort();
  }, []);

  const search = useCallback(
    (query: string) => filterSlashCommands(commands, query),
    [commands],
  );

  return useMemo(() => ({ commands, error, search }), [commands, error, search]);
}
