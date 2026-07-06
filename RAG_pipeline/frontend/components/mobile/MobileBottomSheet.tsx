"use client";

import { useEffect, useRef, useState, type ReactNode, type MouseEvent as ReactMouseEvent } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useVisualViewport } from "@/hooks/useVisualViewport";

export interface MobileBottomSheetProps {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: ReactNode;
  ariaLabel?: string;
  heightClassName?: string;
}

const SWIPE_DISTANCE_THRESHOLD_PX = 80;

export function MobileBottomSheet({
  open,
  onClose,
  title,
  children,
  ariaLabel,
  heightClassName = "max-h-[85vh]",
}: MobileBottomSheetProps) {
  const startYRef = useRef<number | null>(null);
  const draggingRef = useRef(false);
  const [translate, setTranslate] = useState(0);
  const [dragging, setDragging] = useState(false);
  const { keyboardHeight } = useVisualViewport();

  useEffect(() => {
    if (!open) {
      setTranslate(0);
      setDragging(false);
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
    startYRef.current = e.clientY;
    draggingRef.current = true;
    setDragging(true);
  }

  function onMouseMove(e: ReactMouseEvent<HTMLDivElement>) {
    if (!draggingRef.current) return;
    if (startYRef.current === null) return;
    const dy = e.clientY - startYRef.current;
    setTranslate(Math.max(0, dy));
  }

  function onMouseUp(e: ReactMouseEvent<HTMLDivElement>) {
    if (!draggingRef.current) return;
    draggingRef.current = false;
    setDragging(false);
    const dy = e.clientY - (startYRef.current ?? e.clientY);
    if (dy > SWIPE_DISTANCE_THRESHOLD_PX) {
      onClose();
    }
    setTranslate(0);
    startYRef.current = null;
  }

  if (!open) return null;

  const style: React.CSSProperties = {
    transform: `translateY(${translate}px)`,
    paddingBottom: keyboardHeight > 0 ? keyboardHeight : undefined,
  };

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
        className={cn(
          "absolute bottom-0 left-0 right-0 mx-auto bg-card shadow-2xl rounded-t-2xl flex flex-col",
          heightClassName,
          dragging ? "" : "transition-transform duration-200 ease-out",
        )}
        style={style}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseUp}
      >
        <div className="flex items-center justify-center pt-2 pb-1 cursor-grab active:cursor-grabbing">
          <div className="h-1.5 w-10 rounded-full bg-muted-foreground/30" />
        </div>
        <div className="flex items-center justify-between px-4 pb-2 border-b border-border/60">
          {title ? (
            <h2 className="text-base font-semibold text-foreground">{title}</h2>
          ) : (
            <div />
          )}
          <button
            onClick={onClose}
            className="p-2 rounded-md text-muted-foreground hover:bg-muted hover:text-foreground transition-colors min-h-[40px] min-w-[40px] flex items-center justify-center"
            aria-label="Close"
            title="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto overscroll-contain">
          {children}
        </div>
      </div>
    </div>
  );
}
