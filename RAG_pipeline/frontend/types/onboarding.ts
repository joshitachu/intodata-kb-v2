import { z } from "zod";

// Platform + comm-tool IDs are now data-driven (returned by /onboarding/config).
// We keep them as plain strings so a new option shipped via the backend config
// doesn't require a client redeploy.
export type Platform = string;
export type CommTool = string;

export const SourceTypeSchema = z.enum(["github", "zip"]).nullable();
export type SourceType = z.infer<typeof SourceTypeSchema>;

export const SetupStatusSchema = z.enum([
  "pending",
  "processing",
  "completed",
  "failed",
  "skipped",
]);
export type SetupStatus = z.infer<typeof SetupStatusSchema>;

export const OnboardingStatusSchema = z.object({
  wizard_completed: z.boolean(),
  wizard_current_step: z.number(),
  tour_completed: z.boolean(),
  tour_current_step: z.number(),
  setup_status: SetupStatusSchema,
  setup_error: z.string().nullable().optional(),
  workspace_name: z.string().nullable().optional(),
  // Config is data-driven — we don't whitelist at the frontend any more, the
  // backend tells us which ids are valid. Keep string arrays so new options
  // shipped via config don't require a client redeploy.
  selected_platforms: z.array(z.string()).default([]),
  selected_comm_tools: z.array(z.string()).default([]),
  source_type: SourceTypeSchema.optional(),
  source_url: z.string().nullable().optional(),
  source_filename: z.string().nullable().optional(),
});

export type OnboardingStatus = z.infer<typeof OnboardingStatusSchema>;

export const SetupStatusResponseSchema = z.object({
  setup_status: SetupStatusSchema,
  setup_error: z.string().nullable().optional(),
});

export type SetupStatusResponse = z.infer<typeof SetupStatusResponseSchema>;

export const CompleteWizardResponseSchema = z.object({
  wizard_completed: z.boolean(),
  setup_status: SetupStatusSchema,
});

export type CompleteWizardResponse = z.infer<typeof CompleteWizardResponseSchema>;

export const NotificationSchema = z.object({
  id: z.number(),
  type: z.string(),
  title: z.string(),
  message: z.string(),
  is_read: z.boolean(),
  meta: z.record(z.unknown()).nullable().optional(),
  created_at: z.string(),
});

export type Notification = z.infer<typeof NotificationSchema>;

export const NotificationListSchema = z.object({
  notifications: z.array(NotificationSchema),
  unread_count: z.number(),
});

export type NotificationList = z.infer<typeof NotificationListSchema>;

export const UnreadCountSchema = z.object({
  unread_count: z.number(),
});

export type UnreadCount = z.infer<typeof UnreadCountSchema>;

// Data-driven config from GET /onboarding/config
export const OptionStatusSchema = z.enum(["available", "coming_soon"]);
export type OptionStatus = z.infer<typeof OptionStatusSchema>;

export const OptionItemSchema = z.object({
  id: z.string(),
  label: z.string(),
  description: z.string(),
  status: OptionStatusSchema,
});
export type OptionItem = z.infer<typeof OptionItemSchema>;

export const ToolCategorySchema = z.object({
  id: z.string(),
  label: z.string(),
  tools: z.array(OptionItemSchema),
});
export type ToolCategory = z.infer<typeof ToolCategorySchema>;

export const OnboardingConfigSchema = z.object({
  platforms: z.array(OptionItemSchema),
  tool_categories: z.array(ToolCategorySchema),
});
export type OnboardingConfig = z.infer<typeof OnboardingConfigSchema>;

// Fallback config if the API is unreachable mid-wizard — keeps the wizard
// usable so the user isn't blocked. Must stay in sync with the backend list
// in `src/api/routes/onboarding.py`.
export const FALLBACK_CONFIG: OnboardingConfig = {
  platforms: [
    { id: "talend", label: "Talend", description: "ETL jobs and integration projects", status: "available" },
    { id: "workato", label: "Workato", description: "iPaaS recipes and connectors", status: "available" },
    { id: "lobster", label: "Lobster", description: "Data integration workflows", status: "available" },
    { id: "generic", label: "Generic codebase", description: "Any other integration or service repo", status: "available" },
  ],
  tool_categories: [
    {
      id: "communication",
      label: "Communication",
      tools: [
        { id: "slack", label: "Slack", description: "Team chat and alerts", status: "coming_soon" },
        { id: "teams", label: "Microsoft Teams", description: "Microsoft 365 chat", status: "coming_soon" },
      ],
    },
    {
      id: "ticketing",
      label: "Ticketing & Docs",
      tools: [
        { id: "jira", label: "Jira", description: "Issue tracking and tickets", status: "coming_soon" },
        { id: "confluence", label: "Confluence", description: "Internal wiki and docs", status: "coming_soon" },
      ],
    },
  ],
};
