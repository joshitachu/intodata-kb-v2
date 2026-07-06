"use client";

import type { ReactNode } from "react";
import { useViewport } from "./useViewport";

export type MobileTitle = {
  title: string;
  rightAction?: ReactNode;
};

export function useMobileTitle(title: string, rightAction?: ReactNode): MobileTitle | null {
  const { isMobile } = useViewport();
  if (!isMobile) return null;
  return { title, rightAction };
}
