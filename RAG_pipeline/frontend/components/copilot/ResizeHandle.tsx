"use client";

import { useCallback, useRef } from "react";
import { cn } from "@/lib/utils";

/** How far one arrow-key press moves the edge. */
const KEYBOARD_STEP = 24;

export interface ResizeHandleProps {
  /** Current width of the panel to the RIGHT of this handle, in pixels. */
  width: number;
  onWidthChange: (width: number) => void;
  /** Restore the default width. Bound to double-click and to Home. */
  onReset: () => void;
  label?: string;
}

/**
 * The draggable left edge of a right-docked panel.
 *
 * Reports a WIDTH rather than a delta, computed from the viewport edge:
 * `innerWidth - clientX`. A delta-based handle accumulates rounding drift over a
 * long drag and desynchronises from the cursor; deriving the width from the
 * pointer's absolute position cannot drift, because every event recomputes it
 * from scratch.
 *
 * Pointer capture rather than window listeners: the pointer leaves this 5px
 * strip on the first frame of any real drag, and capture is what keeps the
 * events coming without a listener that outlives the gesture. It also ends the
 * drag correctly when the pointer is released outside the window, which a
 * `mouseup` listener on the strip does not.
 *
 * Exposed as `role="separator"` with `aria-valuenow`, which is the ARIA window
 * splitter pattern: the same resize has to be reachable without a pointer, and
 * arrow keys on a focusable separator is what assistive technology expects.
 */
export function ResizeHandle({
  width,
  onWidthChange,
  onReset,
  label = "Resize panel",
}: ResizeHandleProps) {
  const draggingRef = useRef(false);

  const handlePointerDown = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    // Secondary buttons open context menus; starting a drag on one strands the
    // handle in a captured state the user never asked for. Tested as "> 0"
    // rather than "!== 0" so an environment that does not populate `button`
    // still drags: jsdom has no PointerEvent constructor and dispatches a plain
    // Event, where `button` is undefined and `!== 0` would reject every press.
    if (event.button > 0) return;
    draggingRef.current = true;
    // Capture is an enhancement, not a requirement: without it the drag still
    // tracks while the pointer is over the strip. Letting an unsupported call
    // throw here would abort the handler and leave the drag half-armed.
    event.currentTarget.setPointerCapture?.(event.pointerId);
    event.preventDefault();
  }, []);

  const handlePointerMove = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      if (!draggingRef.current) return;
      onWidthChange(window.innerWidth - event.clientX);
    },
    [onWidthChange],
  );

  const endDrag = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    if (!draggingRef.current) return;
    // Cleared before the release, so an unsupported capture API cannot leave the
    // handle permanently dragging.
    draggingRef.current = false;
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
      event.currentTarget.releasePointerCapture?.(event.pointerId);
    }
  }, []);

  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      // A right-docked panel grows LEFTWARDS, so ArrowLeft widens it. Matching
      // the key to the direction of travel on screen rather than to the numeric
      // value is what makes this feel right; the reverse reads as inverted.
      if (event.key === "ArrowLeft") {
        onWidthChange(width + KEYBOARD_STEP);
      } else if (event.key === "ArrowRight") {
        onWidthChange(width - KEYBOARD_STEP);
      } else if (event.key === "Home") {
        onReset();
      } else {
        return;
      }
      event.preventDefault();
    },
    [onReset, onWidthChange, width],
  );

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuenow={Math.round(width)}
      tabIndex={0}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onDoubleClick={onReset}
      onKeyDown={handleKeyDown}
      className={cn(
        "group relative w-1.5 shrink-0 cursor-col-resize touch-none select-none",
        "bg-border/40 transition-colors hover:bg-primary/40",
        "focus-visible:outline-none focus-visible:bg-primary/60",
      )}
      title="Drag to resize. Double-click to reset."
    >
      {/* The visible strip is 6px so it does not read as a second border, but a
          6px pointer target is a miss on most attempts. This widens the hit area
          to 15px without widening the layout box. */}
      <span aria-hidden className="absolute inset-y-0 -left-1.5 -right-1.5" />
    </div>
  );
}
