"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/**
 * Retired surface, kept as a thin alias.
 *
 * `/labs/chat` used to be a deliberate clone of the main chat: it mounted the
 * same `ChatWindow` inside a second `ChatProvider` with `apiBase="/api/labs"`,
 * so a side-by-side comparison measured the agent rather than the renderer.
 * That comparison is over. The lane is now a first-class choice on the one chat
 * surface (`components/chat/LaneSwitch.tsx`), and keeping a second live
 * implementation would mean every future chat change had to be made twice --
 * which is exactly how the two would drift apart and make the lanes
 * incomparable again.
 *
 * It redirects rather than 404s, and redirects *with the lane* rather than to a
 * bare `/`, because the links and bookmarks that point here were pointing at
 * the OpenCode lane, not merely at chat. `?c=new` starts a thread rather than
 * reopening whatever was last open, since the lane can only be chosen for a new
 * one; `ChatProvider` reads `lane` once, on mount, and the switch owns it
 * afterwards.
 */
const LANE_V2_NEW_CHAT = "/?c=new&lane=v2";

export default function LabsChatAliasPage() {
  const router = useRouter();

  useEffect(() => {
    router.replace(LANE_V2_NEW_CHAT);
  }, [router]);

  return (
    <div className="flex h-full min-h-0 flex-col items-center justify-center p-6 text-center">
      <p className="text-sm text-muted-foreground">
        Labs chat has moved into the main chat. Opening a new V2 thread...
      </p>
      {/* A real link, not only the effect: if the redirect is blocked or
          JavaScript navigation fails, the page still offers the way on. */}
      <a
        href={LANE_V2_NEW_CHAT}
        className="mt-2 text-sm font-medium text-primary underline underline-offset-4"
      >
        Go to chat
      </a>
    </div>
  );
}
