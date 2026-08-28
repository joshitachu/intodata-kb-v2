"use client";

import { useEffect, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  FileSearch,
  Filter,
  Layers3,
  MessageSquareQuote,
  ScanSearch,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface Props {
  onBack: () => void;
  onNext: () => void;
}

/**
 * Explains why answers can be trusted, as a walkable pipeline.
 *
 * This exists because "an AI answers your questions" sets the wrong
 * expectation: people either over-trust it or dismiss it. Showing that the
 * question is scoped, filtered, ranked and cited *before* a model sees it
 * reframes the product as auditable retrieval, which is what it is.
 *
 * Auto-advances once so the animation is seen, then hands control to the user —
 * a loop that keeps moving while someone is reading is an irritation.
 */

const STAGES = [
  {
    icon: Filter,
    title: "Your question is scoped",
    detail:
      "Only evidence from projects you have access to is eligible. Authorization happens before search, not after.",
  },
  {
    icon: ScanSearch,
    title: "Two searches run at once",
    detail:
      "One finds passages that mean the same thing; the other protects exact names like job and table identifiers.",
  },
  {
    icon: Layers3,
    title: "Results are re-ranked",
    detail:
      "Candidates are scored against your actual question, and weak matches are dropped rather than padded out.",
  },
  {
    icon: FileSearch,
    title: "Context is rebuilt",
    detail:
      "Neighbouring sections are pulled back in, so the answer is not written from one isolated sentence.",
  },
  {
    icon: MessageSquareQuote,
    title: "The answer cites its sources",
    detail:
      "Every claim points at the document and section behind it, so you can check it instead of trusting it.",
  },
] as const;

const AUTOPLAY_MS = 2200;

export function GroundingStep({ onBack, onNext }: Props) {
  const [active, setActive] = useState(0);
  const [autoplay, setAutoplay] = useState(true);

  useEffect(() => {
    if (!autoplay) return;
    if (active >= STAGES.length - 1) {
      setAutoplay(false);
      return;
    }
    const timer = setTimeout(() => setActive((i) => i + 1), AUTOPLAY_MS);
    return () => clearTimeout(timer);
  }, [autoplay, active]);

  const select = (index: number) => {
    setAutoplay(false);
    setActive(index);
  };

  return (
    <div className="animate-step-enter mx-auto w-full max-w-2xl px-6">
      <div className="mb-7">
        <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-primary">
          How it works
        </p>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">
          Answers are retrieved, then cited — not guessed
        </h1>
        <p className="mt-3 text-sm leading-6 text-muted-foreground">
          Five things happen between your question and the answer. The model is
          only the last one.
        </p>
      </div>

      {/* Stage rail */}
      <ol className="mb-5 flex gap-1.5" aria-label="Retrieval stages">
        {STAGES.map((stage, index) => (
          <li key={stage.title} className="flex-1">
            <button
              type="button"
              onClick={() => select(index)}
              aria-current={index === active ? "step" : undefined}
              aria-label={`Stage ${index + 1}: ${stage.title}`}
              className="group w-full focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:ring-offset-2 focus-visible:ring-offset-background"
            >
              <span
                className={cn(
                  "block h-1 rounded-full transition-colors duration-300",
                  index === active
                    ? "bg-primary"
                    : index < active
                      ? "bg-primary/40"
                      : "bg-border group-hover:bg-border/70",
                )}
              />
            </button>
          </li>
        ))}
      </ol>

      <div className="rounded-xl border border-border bg-card p-5">
        {STAGES.map((stage, index) => {
          if (index !== active) return null;
          const Icon = stage.icon;
          return (
            // Keyed so the enter animation replays on each stage change.
            <div key={stage.title} className="animate-step-enter flex gap-4">
              <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
                <Icon className="h-5 w-5" aria-hidden />
              </span>
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                  Stage {index + 1} of {STAGES.length}
                </p>
                <h2 className="mt-1 text-base font-semibold text-foreground">
                  {stage.title}
                </h2>
                <p className="mt-1.5 text-sm leading-6 text-muted-foreground">
                  {stage.detail}
                </p>
              </div>
            </div>
          );
        })}
      </div>

      <p aria-live="polite" className="sr-only">
        {`Stage ${active + 1}: ${STAGES[active].title}. ${STAGES[active].detail}`}
      </p>

      <div className="mt-8 flex items-center justify-between">
        <Button variant="ghost" size="md" onClick={onBack}>
          <ArrowLeft className="h-4 w-4" aria-hidden />
          Back
        </Button>
        <div className="flex items-center gap-2">
          {active < STAGES.length - 1 && (
            <Button variant="secondary" size="md" onClick={() => select(active + 1)}>
              Next stage
            </Button>
          )}
          <Button size="md" onClick={onNext}>
            Continue
            <ArrowRight className="h-4 w-4" aria-hidden />
          </Button>
        </div>
      </div>
    </div>
  );
}
