"use client";

import { useEffect, useMemo, useState } from "react";
import { deriveLabsTurn, type LabsTurnRecord } from "./labsTurn";
import type { StreamState } from "@/hooks/useStreamMessage";

/**
 * The turn currently on screen, and the last one once it is over.
 *
 * `ChatProvider` resets the stream state as soon as the answer is persisted, so
 * a panel that read the state directly would lose the tool sequence, the
 * reasoning and the usage line at the exact moment they became worth reading.
 * The derivation is therefore retained: the live projection wins while there is
 * one, and the retained copy takes over when the state goes back to empty.
 *
 * A new turn replaces the retained copy rather than merging into it -- the
 * first frame of the next turn produces a fresh derivation, which wins
 * immediately.
 */
export function useLabsTurnRecord(state: StreamState): LabsTurnRecord | null {
  const derived = useMemo(() => deriveLabsTurn(state), [state]);
  const [retained, setRetained] = useState<LabsTurnRecord | null>(null);

  useEffect(() => {
    // Only a settled turn is worth retaining, and retaining one mid-stream
    // would cost a second render of this subtree per streamed token. Every path
    // out of a turn -- answered, failed, or the reader stopped -- lands on a
    // non-streaming derivation before the chat context clears the state, so the
    // last one is always captured.
    if (derived === null || derived.status === "streaming") return;
    setRetained(derived);
  }, [derived]);

  return derived ?? retained;
}
