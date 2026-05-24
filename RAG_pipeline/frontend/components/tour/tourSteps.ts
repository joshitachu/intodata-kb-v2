import type { DriveStep } from "driver.js";

/**
 * Build the tour step list. Some steps navigate to different pages before
 * revealing — the wrapper handles `router.push()` based on each step's
 * `navigateTo` metadata.
 */
export interface TourStep extends DriveStep {
  navigateTo?: string;
}

export const tourSteps: TourStep[] = [
  {
    element: '[data-tour="chat-input"]',
    navigateTo: "/",
    popover: {
      title: "Ask your knowledge base",
      description:
        "Ask questions about uploaded documents, generated project docs, and your integration knowledge base. Natural language is enough.",
      side: "top",
      align: "center",
    },
  },
  {
    element: '[data-tour="mode-toggle"]',
    navigateTo: "/",
    popover: {
      title: "Choose the work mode",
      description:
        "Chat is for quick answers. Deep Dive runs a project-focused investigation when you need slower, more detailed analysis.",
      side: "bottom",
      align: "center",
    },
  },
  {
    element: '[data-tour="sidebar"]',
    navigateTo: "/",
    popover: {
      title: "Move around the workspace",
      description:
        "Use the sidebar for chat, documents, generated project docs, topology, and admin tools when you have access.",
      side: "right",
      align: "start",
    },
  },
  {
    element: '[data-tour="documents-page"]',
    navigateTo: "/documents",
    popover: {
      title: "Uploaded documents",
      description:
        "Uploaded PDFs, DOCX, PPTX, and other source files appear here after ingestion so you can inspect what the assistant can search.",
      side: "bottom",
      align: "center",
    },
  },
  {
    element: '[data-tour="project-docs-page"]',
    navigateTo: "/docs",
    popover: {
      title: "Generated documentation",
      description:
        "Scanned sources produce project documentation here, including architecture, components, data flows, operations, and risk notes.",
      side: "bottom",
      align: "center",
    },
  },
  {
    element: '[data-tour="topology-page"]',
    navigateTo: "/topology",
    popover: {
      title: "Integration topology",
      description:
        "Use topology to inspect how jobs, groups, connections, and dependencies relate. Select nodes to drill into implementation details.",
      side: "bottom",
      align: "center",
    },
  },
  {
    popover: {
      title: "Ready to start",
      description:
        "Return to chat, pick the right mode, and ask your first question about the workspace.",
    },
  },
];
