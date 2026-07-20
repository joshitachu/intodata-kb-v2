"use client";

import { useEffect, useRef } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

export type ConversationUrlState = {
  activeId: number | null;
  isNew: boolean;
};

const CONVERSATION_PARAM = "c";

function parse(searchParams: URLSearchParams): ConversationUrlState {
  const raw = searchParams.get(CONVERSATION_PARAM);
  if (raw === null) {
    return { activeId: null, isNew: false };
  }
  if (raw === "new") {
    return { activeId: null, isNew: true };
  }
  const parsed = Number.parseInt(raw, 10);
  if (Number.isFinite(parsed) && parsed > 0) {
    return { activeId: parsed, isNew: false };
  }
  return { activeId: null, isNew: false };
}

function buildSearch(searchParams: URLSearchParams, state: ConversationUrlState): string {
  const next = new URLSearchParams(searchParams);
  if (state.activeId !== null) next.set(CONVERSATION_PARAM, String(state.activeId));
  else next.delete(CONVERSATION_PARAM);
  const value = next.toString();
  return value ? `?${value}` : "";
}

export function useUrlConversationId(): ConversationUrlState {
  const searchParams = useSearchParams();
  return parse(searchParams);
}

export function useSyncUrlConversationId(
  activeId: number | null,
  isNew: boolean,
): void {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const lastWrittenRef = useRef<string | null>(null);

  useEffect(() => {
    if (pathname !== "/") return;
    if (activeId === null) return;

    const next = buildSearch(searchParams, { activeId, isNew });
    if (lastWrittenRef.current === next) return;
    lastWrittenRef.current = next;
    router.replace(`${pathname}${next}`);
  }, [activeId, isNew, pathname, router, searchParams]);
}
