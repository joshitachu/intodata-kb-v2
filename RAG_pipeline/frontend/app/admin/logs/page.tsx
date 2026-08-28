"use client";

import { RuntimeConsole } from "@/components/runtime/RuntimeConsole";

export default function RuntimeLogsPage() {
  // Wrapper exists purely to give the walkthrough a stable anchor without
  // threading a tour concern down into the runtime console itself.
  return (
    <div data-tour="admin-logs">
      <RuntimeConsole view="hub" />
    </div>
  );
}
