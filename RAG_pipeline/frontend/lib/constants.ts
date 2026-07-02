import type { LucideIcon } from "lucide-react";
import { FolderOpen, FileText, Network, Waypoints } from "lucide-react";

export interface Suggestion {
  icon: LucideIcon;
  label: string;
}

export const SUGGESTIONS: Suggestion[] = [
  { icon: FolderOpen, label: "What projects are available?" },
  { icon: FileText, label: "Summarize the latest documentation" },
  { icon: Network, label: "How do the integrations connect?" },
  { icon: Waypoints, label: "What are the key data flows?" },
];
