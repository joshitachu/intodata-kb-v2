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
      title: "Ask anything",
      description:
        "This is where you ask questions about your integrations and documents. Natural language — no query syntax needed.",
      side: "top",
      align: "center",
    },
  },
  {
    element: '[data-tour="mode-toggle"]',
    navigateTo: "/",
    popover: {
      title: "Chat vs Deep-Dive",
      description:
        "Chat gives you quick answers. Deep-dive runs a multi-step investigation for complex questions — slower but much more thorough.",
      side: "bottom",
      align: "center",
    },
  },
  {
    element: '[data-tour="project-selector"]',
    navigateTo: "/",
    popover: {
      title: "Scope your question",
      description:
        "Limit search to a single project, or ask across your whole knowledge base.",
      side: "bottom",
      align: "center",
    },
  },
  {
    element: '[data-tour="documents-page"]',
    navigateTo: "/documents",
    popover: {
      title: "Your documents",
      description:
        "Anything you upload — PDFs, DOCX, PPTX — will appear here. We chunk and embed them for retrieval.",
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
        "Once we scan your codebase, auto-generated docs for each project will live here — architecture, data flows, and more.",
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
        "Once your codebase is scanned, you'll see how your integrations connect here. Click any node to drill into its details.",
      side: "bottom",
      align: "center",
    },
  },
  {
    element: '[data-tour="sidebar-nav"]',
    navigateTo: "/",
    popover: {
      title: "Navigation",
      description:
        "Jump between chat, documents, docs, and topology anytime. New conversations start with the + button at the top.",
      side: "right",
      align: "start",
    },
  },
  {
    popover: {
      title: "You're all set!",
      description:
        "That's the whirlwind tour. Head to the chat and ask your first question.",
    },
  },
];
