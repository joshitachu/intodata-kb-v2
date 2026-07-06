"use client";

import { useEffect, useRef, useState, type ReactNode, type MouseEvent as ReactMouseEvent } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

export interface MobileDrawerProps {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  side?: "left" | "right";
  widthClassName?: string;
  ariaLabel?: string;
}

const SWIPE_DISTANCE_THRESHOLD_PX = 60;

export function MobileDrawer({
  open,
  onClose,
  children,
  side = "left",
  widthClassName = "w-[85%] max-w-sm",
  ariaLabel,
}: MobileDrawerProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const startXRef = useRef<number | null>(null);
  const startYRef = useRef<number | null>(null);
  const draggingRef = useRef(false);
  const [translate, setTranslate] = useState(0);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    if (!open) {
      setTranslate(0);
      setDragging(false);
      draggingRef.current = false;
      startXRef.current = null;
      startYRef.current = null;
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  function onMouseDown(e: ReactMouseEvent<HTMLDivElement>) {
    if (side !== "left") return;
    startXRef.current = e.clientX;
    startYRef.current = e.clientY;
    draggingRef.current = true;
    setDragging(true);
  }

  function onMouseMove(e: ReactMouseEvent<HTMLDivElement>) {
    if (!draggingRef.current) return;
    if (startXRef.current === null || startYRef.current === null) return;
    const dx = e.clientX - startXRef.current;
    const dy = e.clientY - startYRef.current;
    if (Math.abs(dy) > Math.abs(dx)) {
      draggingRef.current = false;
      setDragging(false);
      setTranslate(0);
      return;
    }
    setTranslate(Math.min(0, dx));
  }

  function onMouseUp(e: ReactMouseEvent<HTMLDivElement>) {
    if (!draggingRef.current) return;
    draggingRef.current = false;
    setDragging(false);
    const dx = e.clientX - (startXRef.current ?? e.clientX);
    if (dx < -SWIPE_DISTANCE_THRESHOLD_PX) {
      onClose();
    }
    setTranslate(0);
    startXRef.current = null;
    startYRef.current = null;
  }

  function onMouseLeave() {
    if (!draggingRef.current) return;
    draggingRef.current = false;
    setDragging(false);
    setTranslate(0);
    startXRef.current = null;
    startYRef.current = null;
  }

  if (!open) return null;

  const transform =
    side === "left"
      ? `translateX(${translate}px)`
      : `translateX(${-translate}px)`;

  return (
    <div
      className="fixed inset-0 z-50"
      role="dialog"
      aria-modal="true"
      aria-label={ariaLabel}
    >
      <div
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden
      />
      <div
        ref={panelRef}
        className={cn(
          "absolute top-0 bottom-0 bg-card shadow-xl flex flex-col",
          side === "left" ? "left-0" : "right-0",
          widthClassName,
          dragging ? "" : "transition-transform duration-200 ease-out",
        )}
        style={{ transform }}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseLeave}
      >
        <button
          onClick={onClose}
          className="absolute top-3 right-3 z-10 p-2 rounded-md text-muted-foreground hover:bg-muted hover:text-foreground transition-colors min-h-[44px] min-w-[44px] flex items-center justify-center"
          aria-label="Close menu"
          title="Close"
        >
          <X className="h-4 w-4" />
        </button>
        {children}
      </div>
    </div>
  );
}
