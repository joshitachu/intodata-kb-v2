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

function buildSearch(state: ConversationUrlState): string {
  if (state.activeId !== null) {
    return `?${CONVERSATION_PARAM}=${state.activeId}`;
  }
  if (state.isNew) {
    return `?${CONVERSATION_PARAM}=new`;
  }
  return "";
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
  const lastWrittenRef = useRef<string | null>(null);

  useEffect(() => {
    if (pathname !== "/") return;
    const next = buildSearch({ activeId, isNew });
    if (lastWrittenRef.current === next) return;
    lastWrittenRef.current = next;
    if (next) {
      router.replace(`${pathname}${next}`);
    } else {
      router.replace(pathname);
    }
  }, [activeId, isNew, pathname, router]);
}
