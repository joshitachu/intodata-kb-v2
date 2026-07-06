import { describe, it, expect, vi, beforeEach } from "vitest";

const mockReplace = vi.fn();
const mockSearchParams = { value: new URLSearchParams() };
const mockPathname = { value: "/" };

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockReplace, push: vi.fn(), back: vi.fn() }),
  usePathname: () => mockPathname.value,
  useSearchParams: () => mockSearchParams.value,
}));

import { renderHook, act } from "@testing-library/react";
import {
  useUrlConversationId,
  useSyncUrlConversationId,
} from "@/hooks/useUrlConversationId";

describe("useUrlConversationId", () => {
  beforeEach(() => {
    mockReplace.mockClear();
    mockSearchParams.value = new URLSearchParams();
    mockPathname.value = "/";
  });

  it("returns null activeId when ?c= is absent", () => {
    const { result } = renderHook(() => useUrlConversationId());
    expect(result.current).toEqual({ activeId: null, isNew: false });
  });

  it("parses ?c=42 as a numeric activeId", () => {
    mockSearchParams.value = new URLSearchParams("?c=42");
    const { result } = renderHook(() => useUrlConversationId());
    expect(result.current).toEqual({ activeId: 42, isNew: false });
  });

  it("parses ?c=new as the new-chat sentinel", () => {
    mockSearchParams.value = new URLSearchParams("?c=new");
    const { result } = renderHook(() => useUrlConversationId());
    expect(result.current).toEqual({ activeId: null, isNew: true });
  });

  it("ignores non-numeric ?c= values", () => {
    mockSearchParams.value = new URLSearchParams("?c=banana");
    const { result } = renderHook(() => useUrlConversationId());
    expect(result.current).toEqual({ activeId: null, isNew: false });
  });
});

describe("useSyncUrlConversationId", () => {
  beforeEach(() => {
    mockReplace.mockClear();
    mockPathname.value = "/";
  });

  it("writes ?c={id} when activeId is set", () => {
    renderHook(() => useSyncUrlConversationId(42, false));
    expect(mockReplace).toHaveBeenCalledWith("/?c=42");
  });

  it("does not write ?c=new when isNew is true and activeId is null", () => {
    renderHook(() => useSyncUrlConversationId(null, true));
    expect(mockReplace).not.toHaveBeenCalled();
  });

  it("does not clear the query when both activeId and isNew are falsy", () => {
    renderHook(() => useSyncUrlConversationId(null, false));
    expect(mockReplace).not.toHaveBeenCalled();
  });

  it("coalesces rapid changes (single write per value)", () => {
    const { rerender } = renderHook(
      ({ id, isNew }: { id: number | null; isNew: boolean }) =>
        useSyncUrlConversationId(id, isNew),
      { initialProps: { id: 1 as number | null, isNew: false } },
    );
    rerender({ id: 1, isNew: false });
    rerender({ id: 1, isNew: false });
    expect(mockReplace).toHaveBeenCalledTimes(1);
  });
});
