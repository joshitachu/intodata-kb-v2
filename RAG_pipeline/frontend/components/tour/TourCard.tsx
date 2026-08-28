"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Check, Lightbulb, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { TargetRect } from "./geometry";
import { place, type Placement } from "./placement";
import type { TourStep } from "./types";

/** A contiguous run of steps sharing a chapter label. */
export interface Chapter {
  label: string;
  /** Index of the chapter's first step within the visible step list. */
  from: number;
  count: number;
}

interface Props {
  step: TourStep;
  rect: TargetRect | null;
  index: number;
  total: number;
  chapterLabel: string;
  /** Chapters of the whole walkthrough, in order, for the progress display. */
  chapters: Chapter[];
  /** Jump past the current chapter. Undefined on the last chapter. */
  onSkipChapter?: () => void;
  /** False fades the card out in place while the next step is resolving. */
  visible: boolean;
  /** True animates the move; false repositions instantly (while invisible). */
  glide: boolean;
  onNext: () => void;
  onPrev: () => void;
  onSkip: () => void;
  isLast: boolean;
}

const MOVE_MS = 300;
const EASE = "cubic-bezier(0.32, 0.72, 0, 1)";

export function TourCard({
  step,
  rect,
  index,
  total,
  chapterLabel,
  chapters,
  onSkipChapter,
  visible,
  glide,
  onNext,
  onPrev,
  onSkip,
  isLast,
}: Props) {
  const cardRef = useRef<HTMLDivElement>(null);
  const nextRef = useRef<HTMLButtonElement>(null);
  const [placement, setPlacement] = useState<Placement>({
    top: -9999,
    left: -9999,
    side: "center",
  });

  // Measure before paint so the card never flashes at the wrong position.
  useLayoutEffect(() => {
    const reposition = () => {
      const height = cardRef.current?.offsetHeight ?? 240;
      // A "center" step is centered by having no rect to anchor to, which is
      // also the fallback when a target could not be resolved, and when the
      // target is too large for a cutout to mean anything.
      const preferred = step.side === "center" ? "bottom" : step.side ?? "bottom";
      setPlacement(
        place(step.side === "center" ? null : rect, preferred, height, {
          width: window.innerWidth,
          height: window.innerHeight,
        }),
      );
    };
    reposition();
    window.addEventListener("resize", reposition);

    // The card's height changes with its copy, and a stale height puts a
    // "top"-side card the wrong distance from its target.
    const observer =
      typeof ResizeObserver === "undefined" || !cardRef.current
        ? null
        : new ResizeObserver(reposition);
    if (observer && cardRef.current) observer.observe(cardRef.current);

    return () => {
      window.removeEventListener("resize", reposition);
      observer?.disconnect();
    };
  }, [rect, step.side, step.id]);

  // Move focus to the primary action on each step so keyboard and screen-reader
  // users advance through the tour the same way pointer users do. Deferred
  // until the card is actually visible: focusing a faded-out card would put the
  // focus ring somewhere nobody can see.
  useEffect(() => {
    if (visible) nextRef.current?.focus();
  }, [step.id, visible]);

  const transition = [
    `opacity 200ms ${EASE}`,
    `transform ${MOVE_MS}ms ${EASE}`,
    // Only animate position when the card is on screen and the move is a small
    // one. Animating it while invisible would leave it mid-flight at the moment
    // it fades back in.
    ...(glide && visible ? [`top ${MOVE_MS}ms ${EASE}`, `left ${MOVE_MS}ms ${EASE}`] : []),
  ].join(", ");

  return (
    <div
      ref={cardRef}
      role="dialog"
      aria-modal="false"
      aria-labelledby={`tour-title-${step.id}`}
      aria-describedby={`tour-body-${step.id}`}
      data-spotlight-animated
      className={cn(
        "fixed z-[101] w-[372px] max-w-[calc(100vw-24px)]",
        "rounded-xl border border-border bg-card p-5 text-card-foreground shadow-elevation-lg",
      )}
      style={{
        top: placement.top,
        left: placement.left,
        opacity: visible ? 1 : 0,
        transform: visible ? "none" : "translate3d(0, 8px, 0) scale(0.985)",
        // An invisible card must not swallow clicks meant for the app.
        pointerEvents: visible ? "auto" : "none",
        transition,
      }}
    >
      {/* Keyed so a step change replays the content cue: the card itself moves,
          but the copy inside it reads as replaced rather than rewritten. */}
      <div key={step.id} className="tour-card-content" data-spotlight-animated>
        <div className="flex items-start justify-between gap-3">
          <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-primary">
            {chapterLabel}
          </p>
          <button
            type="button"
            onClick={onSkip}
            aria-label="Close walkthrough"
            className="-mr-1 -mt-1 rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <X className="h-4 w-4" aria-hidden />
          </button>
        </div>

        <h2
          id={`tour-title-${step.id}`}
          className="mt-2 text-lg font-semibold leading-snug tracking-tight text-foreground"
        >
          {step.title}
        </h2>
        <p
          id={`tour-body-${step.id}`}
          className="mt-2 text-sm leading-6 text-muted-foreground"
        >
          {step.body}
        </p>

        {step.tryIt && (
          <div className="mt-4 rounded-lg border border-primary/25 bg-primary/[0.06] p-3">
            <p className="flex items-center gap-2 text-xs font-semibold text-foreground">
              <Lightbulb className="h-3.5 w-3.5 text-primary" aria-hidden />
              {step.tryIt.label}
            </p>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              {step.tryIt.hint}
            </p>
          </div>
        )}

        <div className="mt-5 flex items-center justify-between gap-3 border-t border-border pt-4">
          <div className="flex min-w-0 items-center gap-2">
            {/* One segment per chapter, widths proportional to their length,
                the current one filled to the step you are on.

                This replaced one dot per step. At six steps that read as "this
                is nearly over"; at twenty-six it is an unreadable row of ticks
                that reads as "this will never end". Chapters are the unit
                people actually care about — how many topics are left, not how
                many clicks. */}
            <div className="flex min-w-0 flex-1 items-center gap-1" aria-hidden>
              {chapters.map((chapter) => {
                const done = index >= chapter.from + chapter.count;
                const current = !done && index >= chapter.from;
                const progress = current
                  ? (index - chapter.from + 1) / chapter.count
                  : done
                    ? 1
                    : 0;
                return (
                  <span
                    key={`${chapter.label}-${chapter.from}`}
                    className="h-1.5 min-w-3 overflow-hidden rounded-full bg-border"
                    style={{ flexGrow: chapter.count, flexBasis: 0 }}
                  >
                    <span
                      className={cn(
                        "block h-full rounded-full transition-all duration-300",
                        current ? "bg-primary" : "bg-primary/45",
                      )}
                      style={{ width: `${progress * 100}%` }}
                    />
                  </span>
                );
              })}
            </div>
            <span className="shrink-0 text-[11px] font-medium text-muted-foreground">
              {index + 1}/{total}
            </span>
          </div>

          <div className="flex shrink-0 items-center gap-2">
            {index > 0 && (
              <Button variant="ghost" size="sm" onClick={onPrev}>
                <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
                Back
              </Button>
            )}
            {/* An escape hatch for the long administrative chapters: a reader
                who does not run the deployment should not have to click
                through eight steps about it to reach the end. */}
            {onSkipChapter && (
              <Button variant="ghost" size="sm" onClick={onSkipChapter}>
                Skip section
              </Button>
            )}
            <Button ref={nextRef} size="sm" onClick={onNext}>
              {isLast ? "Finish" : "Next"}
              {isLast ? (
                <Check className="h-3.5 w-3.5" aria-hidden />
              ) : (
                <ArrowRight className="h-3.5 w-3.5" aria-hidden />
              )}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
