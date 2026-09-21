"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  useEffect,
  useRef,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  useConversations,
  useConversation,
  useCreateConversation,
  useDeleteConversation,
} from "@/hooks/useConversations";
import { useStreamMessage } from "@/hooks/useStreamMessage";
import type { StreamState } from "@/hooks/useStreamMessage";
import type {
  ChatLane,
  ConversationResponse,
  ConversationDetailResponse,
  ConversationMessageRequest,
  ConversationTurnMode,
} from "@/types";
import type { ChatMode } from "@/components/ModeToggle";
import {
  DEFAULT_CHAT_LANE,
  LANE_API_BASE,
  laneMatchesFilter,
  parseLane,
  type LaneFilter,
} from "@/components/chat/lane";

interface ChatContextValue {
  // Conversation list
  /**
   * Every thread the user has, in both lanes. Unfiltered on purpose: callers
   * that ask "does this id exist" (ChatLayout's URL recovery) must not be
   * answered by the lane filter, or narrowing the list would bounce the open
   * thread back to `/?c=new`.
   */
  conversations: ConversationResponse[];
  /** `conversations` narrowed by `laneFilter`. What the thread lists render. */
  visibleConversations: ConversationResponse[];
  isLoading: boolean;

  // Active conversation
  activeConversationId: number | null;
  setActiveConversationId: (id: number | null) => void;
  activeConversation: ConversationDetailResponse | null;
  isLoadingConversation: boolean;

  // Lane
  /**
   * The lane in force right now: the open thread's own lane, or -- with no
   * thread open -- the lane the next new thread will be created in.
   */
  activeLane: ChatLane;
  /** The lane the switch is set to for the *next* new thread. */
  selectedLane: ChatLane;
  setSelectedLane: (lane: ChatLane) => void;
  /**
   * True once a thread is open. The lane is settled at creation and no route
   * can change it, so the switch must show the thread's lane and refuse to
   * pretend otherwise.
   */
  isLaneLocked: boolean;
  laneFilter: LaneFilter;
  setLaneFilter: (filter: LaneFilter) => void;

  // Mode & project
  /**
   * The mode the NEXT turn will actually run in.
   *
   * Clamped to `chat` whenever the lane in force cannot honour Deep Dive, so
   * every reader -- the toggle, the composer's own deep-dive affordances, the
   * request body -- sees the same answer. See `isDeepDiveAvailable`.
   */
  mode: ChatMode;
  setMode: (mode: ChatMode) => void;
  /**
   * Whether the lane in force reads a turn's `mode` at all.
   *
   * False on V1: `backend/src/api/routes/conversations.py` never reads the
   * field, so a deep-dive turn sent to it is silently answered as a chat turn.
   * The user's raw choice is still remembered behind this, so picking V2 again
   * on a thread that does not exist yet restores it rather than quietly
   * resetting it.
   */
  isDeepDiveAvailable: boolean;
  selectedProject: string;
  setSelectedProject: (project: string) => void;

  // Actions
  createConversation: () => Promise<number | null>;
  deleteConversation: (id: number) => Promise<void>;
  handleSendMessage: (request: ConversationMessageRequest) => Promise<void>;
  retryFailedMessage: () => Promise<void>;
  refreshActiveConversation: () => void;
  isCreating: boolean;

  // Turn provenance
  /**
   * Assistant messages this session watched run in Deep Dive, by message id.
   *
   * Session-scoped because that is all the truth there is: nothing persists a
   * turn's mode. There is no `mode` column on `conversation_messages`, no
   * `mode` field on the backend's `MessageResponse`, and no SSE frame carries
   * it (`backend/src/harness/agent_runs.py::_PUBLIC_EVENT_FIELDS` is a
   * whitelist and lists no such field). The client knows only what it sent, so
   * it records the pairing the `done` frame hands it -- the mode it sent, and
   * the message id that turn produced.
   *
   * The consequence is deliberate and must not be papered over: after a reload
   * this set is empty and past Deep Dive turns render unbadged. An absent badge
   * is recoverable; a badge inferred from tool names or evidence shape would be
   * wrong on exactly the turns that matter.
   */
  deepDiveMessageIds: ReadonlySet<number>;
  /** True while the turn currently streaming is running in Deep Dive. */
  isDeepDiveTurnInFlight: boolean;

  // Stream state
  pendingUserMessage: string | null;
  streamState: StreamState;
  /**
   * Stops reading the turn in progress (`hooks/useStreamMessage.cancelStream`).
   *
   * Client-side only: there is no cancel endpoint on either lane, so the run
   * may still finish on the server. Surfaces that offer this must say so.
   * Nothing in production chat calls it today; `/labs/chat` does.
   */
  cancelStream: () => void;
}

const ChatContext = createContext<ChatContextValue | null>(null);

export function useChatContext(): ChatContextValue {
  const ctx = useContext(ChatContext);
  if (!ctx) {
    throw new Error("useChatContext must be used within a ChatProvider");
  }
  return ctx;
}

export function useChatContextSafe(): ChatContextValue | null {
  return useContext(ChatContext);
}

/** The one endpoint that lists both lanes, so the one the thread list reads. */
const LIST_API_BASE = LANE_API_BASE.v1;

/** URL parameter that seeds the initial lane, written by the `/labs/chat` alias. */
const LANE_PARAM = "lane";

/**
 * The composer toggle's vocabulary, as the turn carries it.
 *
 * Deep dive used to be a different endpoint (`/api/deep-dive/query` -> the MCP
 * server's own engine). It is a mode of the same agent on the same thread now,
 * so the toggle only decides one field of the normal chat request; the hyphen
 * is the toggle's spelling, the underscore is the wire's.
 */
const TURN_MODE: Record<ChatMode, ConversationTurnMode> = {
  chat: "chat",
  "deep-dive": "deep_dive",
};

/**
 * The only lane whose runtime reads a turn's `mode`.
 *
 * V1 accepts the field and never looks at it, so the choice has to be refused
 * here rather than forwarded; see `isDeepDiveAvailable` on the context value.
 */
const DEEP_DIVE_LANE: ChatLane = "v2";

function requestedLaneFromLocation(): ChatLane | null {
  if (typeof window === "undefined") return null;
  return parseLane(new URLSearchParams(window.location.search).get(LANE_PARAM));
}

interface ChatProviderProps {
  children: ReactNode;
  /**
   * Lane a new thread starts in before the user touches the switch.
   *
   * There used to be an `apiBase` prop here instead, and `/labs/chat` mounted a
   * second provider with `apiBase="/api/labs"` to reach the OpenCode lane. The
   * lane now decides the base (`components/chat/lane.ts`), so there is one
   * provider and one surface: a prop that could disagree with the open thread's
   * stored lane is exactly the bug this collapse removes.
   */
  initialLane?: ChatLane;
}

export function ChatProvider({
  children,
  initialLane = DEFAULT_CHAT_LANE,
}: ChatProviderProps) {
  const [activeConversationId, setActiveConversationId] = useState<
    number | null
  >(null);
  const [modeState, setModeState] = useState<ChatMode>("chat");
  const [selectedProject, setSelectedProject] = useState("");
  const [pendingUserMessage, setPendingUserMessage] = useState<string | null>(
    null,
  );
  const [selectedLane, setSelectedLane] = useState<ChatLane>(initialLane);
  const [laneFilter, setLaneFilter] = useState<LaneFilter>("all");
  // Turn provenance. `deepDiveMessageIds` is the session's record of which
  // persisted answers ran in Deep Dive; `inFlightTurnMode` is the one turn
  // currently streaming, which has no message id yet.
  const [deepDiveMessageIds, setDeepDiveMessageIds] = useState<
    ReadonlySet<number>
  >(() => new Set<number>());
  const [inFlightTurnMode, setInFlightTurnMode] =
    useState<ConversationTurnMode | null>(null);
  // The lane of the thread in hand, once anything has established it: the
  // creation call, or the loaded conversation record. The list is the
  // authority, but it has not necessarily refetched at the moment a brand-new
  // thread starts streaming, and it has not necessarily *arrived* when a thread
  // is opened straight from a `?c=` URL -- and both of those turns have to go
  // to the right lane.
  //
  // It is sticky on purpose. A lane read live off `activeConversation` would
  // vanish the instant the query key changed, which is exactly when the lane
  // had just changed it -- the base would flip back, the old cache would answer,
  // and the two would trade places forever.
  const [knownThreadLane, setKnownThreadLane] = useState<{
    id: number;
    lane: ChatLane;
  } | null>(null);
  const justCreatedIdRef = useRef<number | null>(null);
  const failedRequestRef = useRef<ConversationMessageRequest | null>(null);

  // `/labs/chat` is an alias that redirects to `/?c=new&lane=v2`. Read once, on
  // mount, rather than during render: a lazy `useState` initialiser would make
  // the server's markup (which has no `window`) disagree with the client's.
  useEffect(() => {
    const requested = requestedLaneFromLocation();
    if (requested !== null) setSelectedLane(requested);
  }, []);

  const queryClient = useQueryClient();
  // The list is NOT lane-routed. `GET /api/v1/conversations` returns both lanes,
  // each row carrying its own `lane`, which is what makes one badged sidebar
  // possible; reading it from the active lane's base would show the user half
  // their threads and hide the other half behind the switch.
  const { data: conversationList, isLoading: isLoadingList } =
    useConversations(LIST_API_BASE);

  const conversations = useMemo(
    () => conversationList?.conversations ?? [],
    [conversationList],
  );

  // A thread's lane comes from its own record; `selectedLane` only ever answers
  // for the thread that does not exist yet.
  const activeThreadLane = useMemo<ChatLane | null>(() => {
    if (activeConversationId === null) return null;
    const listed = conversations.find((c) => c.id === activeConversationId);
    if (listed) return listed.lane;
    if (knownThreadLane?.id === activeConversationId) return knownThreadLane.lane;
    return null;
  }, [activeConversationId, conversations, knownThreadLane]);

  /**
   * A thread is open whose lane nothing has established yet -- the list is
   * still in flight behind a `?c=` deep link. Read through the production base
   * rather than the switch's current selection: `/api/v1/conversations/{id}`
   * serves BOTH lanes and answers with the thread's own `lane`, so the guess
   * corrects itself from its own response. The V2 base is lane-gated and would
   * 404 a V1 thread instead, which corrects nothing.
   */
  const activeLane =
    activeConversationId === null
      ? selectedLane
      : activeThreadLane ?? DEFAULT_CHAT_LANE;
  const isLaneLocked = activeConversationId !== null;
  const laneApiBase = LANE_API_BASE[activeLane];

  // Clamped here rather than only in the toggle, because the toggle is not the
  // only way `mode` moves: the welcome screen's capability cards set it too,
  // and the lane can be switched *after* Deep Dive was picked while no thread
  // exists. One derivation means the control, the composer's deep-dive
  // affordances and the request body cannot drift apart. `modeState` keeps the
  // user's raw choice so returning to V2 restores it.
  const isDeepDiveAvailable = activeLane === DEEP_DIVE_LANE;
  const mode: ChatMode = isDeepDiveAvailable ? modeState : "chat";

  const visibleConversations = useMemo(
    () => conversations.filter((c) => laneMatchesFilter(c.lane, laneFilter)),
    [conversations, laneFilter],
  );

  const { data: activeConversation, isLoading: isLoadingConversation } =
    useConversation(activeConversationId, laneApiBase);
  // Created in the lane the switch is set to: with no thread open, `activeLane`
  // is `selectedLane`, and a thread is only ever created when none is open.
  const createMutation = useCreateConversation(laneApiBase);
  // Deleting is the one action aimed at a thread other than the open one, so it
  // cannot borrow the active lane. Both mutations are held and the row's own
  // lane picks between them.
  const deleteV1Mutation = useDeleteConversation(LANE_API_BASE.v1);
  const deleteV2Mutation = useDeleteConversation(LANE_API_BASE.v2);

  const { streamState, sendMessage: sendStreamMessage, reset, cancelStream } =
    useStreamMessage(activeConversationId, laneApiBase);

  // Remember the lane the loaded thread reports. This is what closes the deep
  // link case: the detail response carries `lane` whichever base served it, so
  // one read settles the lane before the composer is reachable -- `ChatWindow`
  // shows a spinner until this query resolves.
  useEffect(() => {
    if (!activeConversation || activeConversation.id !== activeConversationId) {
      return;
    }
    setKnownThreadLane((prev) =>
      prev?.id === activeConversation.id && prev.lane === activeConversation.lane
        ? prev
        : { id: activeConversation.id, lane: activeConversation.lane },
    );
  }, [activeConversation, activeConversationId]);

  // When streaming is done (messageId set), invalidate caches and reset
  useEffect(() => {
    if (streamState.messageId !== null) {
      // The `done` frame is the only moment the mode the client sent and the id
      // of the message it produced are both in hand. Record the pairing before
      // `reset()` throws the stream state away; nothing on the server will ever
      // be able to answer this question afterwards.
      const messageId = streamState.messageId;
      if (inFlightTurnMode === "deep_dive" && messageId > 0) {
        setDeepDiveMessageIds((prev) =>
          prev.has(messageId) ? prev : new Set(prev).add(messageId),
        );
      }
      setInFlightTurnMode(null);
      failedRequestRef.current = null;
      queryClient.invalidateQueries({
        queryKey: ["conversation", activeConversationId],
      });
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      reset();
    }
  }, [
    streamState.messageId,
    inFlightTurnMode,
    activeConversationId,
    queryClient,
    reset,
  ]);

  // Clear active conversation if it was deleted (skip for just-created ones)
  useEffect(() => {
    if (
      activeConversationId &&
      conversationList?.conversations &&
      !conversationList.conversations.find(
        (c) => c.id === activeConversationId,
      )
    ) {
      if (activeConversationId === justCreatedIdRef.current) {
        return; // List hasn't refetched yet — don't clear
      }
      setActiveConversationId(null);
    } else if (
      activeConversationId &&
      justCreatedIdRef.current === activeConversationId &&
      conversationList?.conversations?.find(
        (c) => c.id === activeConversationId,
      )
    ) {
      justCreatedIdRef.current = null; // List caught up, clear the ref
      // `knownThreadLane` is deliberately NOT cleared here. The list outranks
      // it anyway, and dropping it is what would let the lane blink back to the
      // default while a query key is in flight.
    }
  }, [activeConversationId, conversationList]);

  // Clear pending user message when stream is done. Both modes stream through
  // the same lane, so this is no longer gated on the mode -- gating it was what
  // made the deep-dive path need its own bookkeeping.
  useEffect(() => {
    if (!streamState.isStreaming && streamState.messageId !== null) {
      setPendingUserMessage(null);
    }
  }, [streamState.isStreaming, streamState.messageId]);

  useEffect(() => {
    if (streamState.error) {
      // A failed turn produced no message to badge, so the in-flight mode is
      // dropped rather than carried into whatever the user sends next.
      setInFlightTurnMode(null);
      setPendingUserMessage(null);
      queryClient.invalidateQueries({
        queryKey: ["conversation", activeConversationId],
      });
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
    }
  }, [activeConversationId, queryClient, streamState.error]);

  /**
   * Create a thread in the lane the switch is set to, and open it.
   *
   * The lane travels in the request body as well as in the base URL: the base
   * is what makes the OpenCode routes answer, and `lane` is what the unified
   * `POST /api/v1/conversations` reads. Both accept the field, and sending it
   * means the stored lane cannot disagree with the lane the user picked.
   *
   * Throws on failure -- the two callers report it differently.
   */
  const startConversation = useCallback(async () => {
    const lane = activeLane;
    const created = await createMutation.mutateAsync({ lane });
    justCreatedIdRef.current = created.id;
    setKnownThreadLane({ id: created.id, lane });
    setActiveConversationId(created.id);
    // The mutation only invalidates its own lane's list key. The sidebar reads
    // the cross-lane list, so refresh by prefix or a new V2 thread would not
    // appear until something else happened to invalidate it.
    queryClient.invalidateQueries({ queryKey: ["conversations"] });
    return created;
  }, [activeLane, createMutation, queryClient]);

  const createConversation = useCallback(async () => {
    try {
      return (await startConversation()).id;
    } catch (error) {
      console.error("Failed to create conversation:", error);
      return null;
    }
  }, [startConversation]);

  const deleteConversation = useCallback(async (id: number) => {
    // Delete is aimed at a row, which may not be the open thread, so the lane
    // comes from that row rather than from `activeLane`.
    const lane =
      conversations.find((c) => c.id === id)?.lane ??
      (knownThreadLane?.id === id ? knownThreadLane.lane : DEFAULT_CHAT_LANE);
    const mutation = lane === "v2" ? deleteV2Mutation : deleteV1Mutation;
    try {
      await mutation.mutateAsync(id);
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      if (activeConversationId === id) {
        setActiveConversationId(null);
      }
    } catch (error) {
      console.error("Failed to delete conversation:", error);
    }
  }, [
    activeConversationId,
    conversations,
    knownThreadLane,
    deleteV1Mutation,
    deleteV2Mutation,
    queryClient,
  ]);

  const handleSendMessage = useCallback(async (request: ConversationMessageRequest) => {
    const stableRequest: ConversationMessageRequest = request.client_turn_id
      ? request
      : { ...request, client_turn_id: globalThis.crypto.randomUUID() };
    let conversationId = activeConversationId;
    if (!conversationId) {
      try {
        conversationId = (await startConversation()).id;
      } catch (error) {
        console.error("Failed to create conversation:", error);
        return;
      }
    }

    setPendingUserMessage(stableRequest.query);

    try {
      // `mode` is already clamped to what the lane can honour, so this is both
      // what is sent and what the turn will really run as -- which is what
      // makes it safe to record against the message the turn produces.
      const turnMode = TURN_MODE[mode];
      setInFlightTurnMode(turnMode);
      const streamRequest = {
        ...stableRequest,
        project_id: stableRequest.project_id || selectedProject || undefined,
        mode: turnMode,
      };
      failedRequestRef.current = streamRequest;
      await sendStreamMessage(
        streamRequest,
        conversationId ?? undefined,
      );
    } catch (error) {
      console.error("Failed to send message:", error);
    }
  }, [activeConversationId, mode, selectedProject, sendStreamMessage, startConversation]);

  const retryFailedMessage = useCallback(async () => {
    const request = failedRequestRef.current;
    if (!request || streamState.isStreaming) {
      return;
    }
    await handleSendMessage(request);
  }, [handleSendMessage, streamState.isStreaming]);

  const refreshActiveConversation = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["conversation", activeConversationId] });
    queryClient.invalidateQueries({ queryKey: ["conversations"] });
  }, [activeConversationId, queryClient]);

  const setMode = useCallback((newMode: ChatMode) => {
    setModeState(newMode);
  }, []);

  const contextValue = useMemo(() => ({
    conversations,
    visibleConversations,
    isLoading: isLoadingList,
    activeConversationId,
    setActiveConversationId,
    activeConversation: activeConversation ?? null,
    isLoadingConversation,
    activeLane,
    selectedLane,
    setSelectedLane,
    isLaneLocked,
    laneFilter,
    setLaneFilter,
    mode,
    setMode,
    isDeepDiveAvailable,
    selectedProject,
    setSelectedProject,
    deepDiveMessageIds,
    isDeepDiveTurnInFlight: inFlightTurnMode === "deep_dive",
    createConversation,
    deleteConversation,
    handleSendMessage,
    retryFailedMessage,
    refreshActiveConversation,
    isCreating: createMutation.isPending,
    pendingUserMessage,
    streamState,
    cancelStream,
  }), [
    conversations,
    visibleConversations,
    isLoadingList,
    activeConversationId,
    activeConversation,
    isLoadingConversation,
    activeLane,
    selectedLane,
    isLaneLocked,
    laneFilter,
    mode,
    setMode,
    isDeepDiveAvailable,
    selectedProject,
    deepDiveMessageIds,
    inFlightTurnMode,
    createConversation,
    deleteConversation,
    handleSendMessage,
    retryFailedMessage,
    refreshActiveConversation,
    createMutation.isPending,
    pendingUserMessage,
    streamState,
    cancelStream,
  ]);

  return (
    <ChatContext.Provider value={contextValue}>
      {children}
    </ChatContext.Provider>
  );
}
