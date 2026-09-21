"use client";

import { useId, useRef, type KeyboardEvent } from "react";
import { Lock } from "lucide-react";
import { useChatContext } from "@/contexts/ChatContext";
import { cn } from "@/lib/utils";
import { CHAT_LANES, LANE_DESCRIPTION, LANE_LABEL } from "./lane";
import type { ChatLane } from "@/types";

export interface LaneSwitchProps {
  /** The lane to display: the open thread's, or the one a new thread will use. */
  lane: ChatLane;
  /** True once a thread is open. See the locked branch below. */
  isLocked: boolean;
  onLaneChange: (lane: ChatLane) => void;
}

const LOCKED_HINT =
  "The lane is fixed when a thread is created. Start a new chat to use the other one.";

/** The heading over the two options, and the group's accessible name. */
const GROUP_LABEL = "Answering engine";

/**
 * What each lane means, in the row itself rather than behind a tooltip.
 *
 * A user picking a runtime should not have to hover to find out what "V2" is,
 * and the two lanes differ along one axis worth stating: V1 answers from
 * retrieval in one pass, V2 runs an agent loop that can take several. Kept to
 * three words so the pair reads as a comparison rather than as documentation.
 */
const LANE_SUMMARY: Readonly<Record<ChatLane, string>> = {
  v1: "Proven, quick answers",
  v2: "Deeper, multi-step research",
};

/** The lane that carries the NEW badge. Exactly one, or the badge says nothing. */
const NEW_LANE: ChatLane = "v2";

/**
 * Picks which reasoning runtime answers the *next* new thread.
 *
 * Two states, and the difference between them is the whole point:
 *
 *   open thread   the thread's own lane, rendered inert -- no buttons, dashed
 *                 border, a padlock, and the reason in the accessible name
 *   no thread     a labelled two-option radio group, V1 preselected
 *
 * The inert branch is not a disabled copy of the live one. A thread's lane is
 * settled at creation and no route can change it: a V2 thread owns an OpenCode
 * session carrying its own history and a V1 thread owns LangGraph state, so a
 * swap would hand the model a conversation it has never seen. Rendering
 * greyed-out buttons would invite a click whose only possible outcome is
 * nothing happening, which reads as a broken control rather than a settled fact.
 *
 * The live branch is a labelled row above the composer rather than another pill
 * in the composer toolbar. The toolbar holds per-turn settings -- mode, project
 * scope -- and a bare `[V1][V2]` sitting among them read as one more of those
 * while being the heaviest choice on the surface: it decides which runtime
 * answers, and it cannot be changed once the thread exists. The locked branch
 * stays compact and keeps the toolbar's weight, because by then there is no
 * choice left to present.
 */
export function LaneSwitch({ lane, isLocked, onLaneChange }: LaneSwitchProps) {
  const labelId = useId();
  // Roving focus: arrow keys move the selection, so the newly selected option
  // has to take focus with it.
  const optionRefs = useRef<Partial<Record<ChatLane, HTMLButtonElement | null>>>({});

  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const step =
      event.key === "ArrowRight" || event.key === "ArrowDown"
        ? 1
        : event.key === "ArrowLeft" || event.key === "ArrowUp"
          ? -1
          : 0;
    if (step === 0) return;
    event.preventDefault();
    const next = CHAT_LANES[(index + step + CHAT_LANES.length) % CHAT_LANES.length];
    onLaneChange(next);
    optionRefs.current[next]?.focus();
  }

  if (isLocked) {
    return (
      <span
        role="note"
        aria-label={`Lane ${LANE_LABEL[lane]}, fixed for this thread. ${LOCKED_HINT}`}
        title={`${LANE_DESCRIPTION[lane]}. ${LOCKED_HINT}`}
        data-testid="lane-switch-locked"
        data-lane={lane}
        className={cn(
          "inline-flex h-7 shrink-0 cursor-default items-center gap-1.5 rounded-full",
          "border border-dashed border-border bg-muted/30 px-2.5",
          "text-xs font-medium text-muted-foreground",
        )}
      >
        <Lock className="h-3 w-3 shrink-0" aria-hidden />
        <span className="font-mono">{LANE_LABEL[lane]}</span>
      </span>
    );
  }

  return (
    <div className="flex flex-col gap-1.5">
      <span
        id={labelId}
        className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground"
      >
        {GROUP_LABEL}
      </span>
      <div
        role="radiogroup"
        aria-labelledby={labelId}
        data-testid="lane-switch"
        className="grid grid-cols-2 gap-2"
      >
        {CHAT_LANES.map((option, index) => {
          const isActive = option === lane;
          return (
            <button
              key={option}
              ref={(node) => {
                optionRefs.current[option] = node;
              }}
              type="button"
              role="radio"
              aria-checked={isActive}
              // One tab stop for the group, as a radio group should have: Tab
              // reaches the current lane, arrow keys move between them.
              tabIndex={isActive ? 0 : -1}
              data-lane={option}
              title={LANE_DESCRIPTION[option]}
              onClick={() => onLaneChange(option)}
              onKeyDown={(event) => handleKeyDown(event, index)}
              className={cn(
                "flex min-w-0 items-start gap-2 rounded-xl border px-3 py-2 text-left transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1",
                isActive
                  ? "border-primary/50 bg-primary/[0.04]"
                  : "border-border bg-card/60 hover:border-primary/40 hover:bg-card/80",
              )}
            >
              <span
                aria-hidden
                className={cn(
                  "mt-[3px] flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border transition-colors",
                  isActive ? "border-primary" : "border-muted-foreground/40",
                )}
              >
                {isActive && <span className="h-1.5 w-1.5 rounded-full bg-primary" />}
              </span>
              <span className="flex min-w-0 flex-col">
                <span className="flex items-center gap-1.5">
                  <span className="font-mono text-xs font-semibold text-foreground">
                    {LANE_LABEL[option]}
                  </span>
                  {option === NEW_LANE && (
                    <span
                      data-testid="lane-new-badge"
                      className={cn(
                        "rounded-full bg-primary/10 px-1.5 py-px",
                        "text-[10px] font-semibold leading-4 tracking-wide text-primary",
                      )}
                    >
                      NEW
                    </span>
                  )}
                </span>
                <span className="mt-0.5 text-[11px] leading-snug text-muted-foreground">
                  {LANE_SUMMARY[option]}
                </span>
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/**
 * The composer's lane control, wired from context.
 *
 * Wired from context rather than from props so `ChatWindow` -- which is shared
 * by every chat entry point -- does not grow a second set of lane props that
 * could disagree with the provider.
 *
 * There used to be a second element here: a "Lane ready (<model>)" health line
 * polling `/api/labs/health` every 30s whenever V2 was in force. It is gone.
 * The composer is not a status board, the line said nothing until something was
 * already broken, and a failing turn reports its own error.
 */
export function ComposerLaneControls() {
  const { activeLane, selectedLane, isLaneLocked, setSelectedLane } =
    useChatContext();

  return (
    <LaneSwitch
      lane={isLaneLocked ? activeLane : selectedLane}
      isLocked={isLaneLocked}
      onLaneChange={setSelectedLane}
    />
  );
}
