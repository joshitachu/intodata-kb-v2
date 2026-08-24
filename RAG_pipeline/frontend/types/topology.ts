import { z } from "zod";
import type { TopologyLevel as CanvasTopologyLevel } from "@/hooks/useTopologyNavigation";

/**
 * Wire contract for the topology drill-down level, in both directions:
 *
 *  - outbound as `page_scope.level` on a chat message (what the user was
 *    looking at when they asked), and
 *  - inbound as `targets[].level` on an answer (where a chip should navigate).
 *
 * The shape mirrors the canvas's own `TopologyLevel` union in
 * `hooks/useTopologyNavigation.ts` EXACTLY -- including its id-vs-name split
 * (`integration` carries ids, `group`/`job`/`component` carry names plus an
 * optional `sourceRoot`) and its camelCase field names, which differ on
 * purpose from the snake_case used everywhere else on the API. The backend
 * pins the same shape in `src/schemas.py` (`TopologyLevel`) and
 * `src/topology/client.py`. Renaming or "normalising" any field here produces
 * chips that navigate nowhere.
 *
 * The schemas are strict, and `sourceRoot` is `.optional()` rather than
 * `.nullable()`: the backend omits absent values instead of serialising null.
 * A payload that sends `null`, or snake_case keys, is therefore rejected as
 * malformed rather than silently navigating to a different (wrong) graph.
 */

/** Bounded token, matching the backend's `TopologyToken`. */
const TopologyTokenSchema = z.string().min(1).max(500);

export const IntegrationSelectionSchema = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("application"), applicationId: TopologyTokenSchema }).strict(),
  z.object({ kind: z.literal("system"), systemId: TopologyTokenSchema }).strict(),
  z.object({ kind: z.literal("edge"), edgeKey: TopologyTokenSchema }).strict(),
  z
    .object({
      kind: z.literal("relationship"),
      source: TopologyTokenSchema,
      target: TopologyTokenSchema,
      streamKeys: z.array(TopologyTokenSchema).max(200).optional().default([]),
    })
    .strict(),
]);

export type IntegrationSelection = z.infer<typeof IntegrationSelectionSchema>;

export const TopologyLevelSchema = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("project") }).strict(),
  z.object({ kind: z.literal("integration"), selection: IntegrationSelectionSchema }).strict(),
  z
    .object({
      kind: z.literal("group"),
      groupName: TopologyTokenSchema,
      sourceRoot: TopologyTokenSchema.optional(),
    })
    .strict(),
  z
    .object({
      kind: z.literal("job"),
      jobName: TopologyTokenSchema,
      sourceRoot: TopologyTokenSchema.optional(),
    })
    .strict(),
  z
    .object({
      kind: z.literal("component"),
      jobName: TopologyTokenSchema,
      componentName: TopologyTokenSchema,
      sourceRoot: TopologyTokenSchema.optional(),
    })
    .strict(),
]);

export type TopologyLevel = z.infer<typeof TopologyLevelSchema>;

// Compile-time drift guard. If the canvas's union and this wire contract ever
// diverge -- a renamed field, a new level, a changed id/name split -- the
// build fails here instead of at runtime with a chip that goes nowhere.
type AssertTrue<T extends true> = T;
type Mirrors<A, B> = A extends B ? true : false;
type _LevelMatchesCanvas = AssertTrue<Mirrors<TopologyLevel, CanvasTopologyLevel>>;
type _CanvasMatchesLevel = AssertTrue<Mirrors<CanvasTopologyLevel, TopologyLevel>>;

/**
 * Whether the answer is asking to MOVE the user or merely naming a place.
 *
 * `navigate` is set by the model only when the user explicitly asked to be
 * taken somewhere ("take me to...", "go back to the view between X and Y").
 * `reference` is everything else, and is the default: an absent, null or
 * unrecognised value resolves to `reference` rather than failing the target,
 * because a malformed payload must never be able to move the user's canvas.
 * That is what `.catch()` buys here -- it swallows both cases in one place.
 */
export const TopologyTargetIntentSchema = z
  .enum(["navigate", "reference"])
  .catch("reference");

export type TopologyTargetIntent = z.infer<typeof TopologyTargetIntentSchema>;

export const TopologyVisualRoleSchema = z
  .enum(["primary", "supporting", "path"])
  .catch("supporting");

export type TopologyVisualRole = z.infer<typeof TopologyVisualRoleSchema>;

export const TopologyMappingVisualDetailSchema = z.object({
  kind: z.literal("mapping"),
  tables: z.array(TopologyTokenSchema).max(8),
  columns: z.array(z.object({
    table: TopologyTokenSchema,
    column: TopologyTokenSchema,
  }).strict()).max(10),
  edges: z.array(z.object({
    target_table: TopologyTokenSchema,
    target_column: TopologyTokenSchema,
  }).strict()).max(10),
}).strict();

export type TopologyMappingVisualDetail = z.infer<
  typeof TopologyMappingVisualDetailSchema
>;

/**
 * One navigable target returned alongside an answer. The envelope is
 * deliberately non-strict (a future sibling field must not invalidate every
 * chip in the answer) while `level` stays strict, because an unexpected key
 * there means a contract mismatch that would mis-navigate.
 */
export const TopologyTargetSchema = z.object({
  label: z.string().min(1).max(200),
  level: TopologyLevelSchema,
  intent: TopologyTargetIntentSchema,
  visual_role: TopologyVisualRoleSchema,
  visual_detail: TopologyMappingVisualDetailSchema.optional(),
});

type ParsedTopologyTarget = z.infer<typeof TopologyTargetSchema>;
/** Input-compatible with pre-Wave3 callers; parsing fills `supporting`. */
export type TopologyTarget = Omit<ParsedTopologyTarget, "visual_role"> & {
  visual_role?: TopologyVisualRole;
};

export const TopologyTargetListSchema = z.array(TopologyTargetSchema).max(50);

/** What the client was looking at while asking. Untrusted prompt hint only. */
export const TopologyPageScopeSchema = z
  .object({
    surface: z.literal("topology"),
    project_id: z.string().min(1).max(255),
    level: TopologyLevelSchema,
    selected_entity: z.object({
      kind: z.enum([
        "application",
        "system",
        "relationship",
        "edge",
        "group",
        "job",
        "component",
        "table",
        "column",
        "mapping",
      ]),
      label: z.string().min(1).max(200),
      entity_id: z.string().min(1).max(500).optional(),
    }).strict().nullish().transform((value) => value ?? undefined),
    active_detail_tab: z.string().min(1).max(100).nullish().transform((value) => value ?? undefined),
    filters: z.array(z.object({
      name: z.string().min(1).max(100),
      value: z.string().min(1).max(200),
    }).strict()).max(12).nullish().transform((value) => value ?? undefined),
  })
  .strict();

export type TopologyPageScope = z.infer<typeof TopologyPageScopeSchema>;

/** `surface` is the discriminator: other surfaces join this union later. */
export const PageScopeSchema = TopologyPageScopeSchema;

export type PageScope = z.infer<typeof PageScopeSchema>;
