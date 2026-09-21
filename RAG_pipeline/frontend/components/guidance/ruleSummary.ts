import {
  EvidenceRulePayloadSchema,
  StylePreferenceSchema,
  VocabularyPayloadSchema,
  AUDIENCE_LABELS,
  COMMUNICATION_LABELS,
  DEPTH_LABELS,
  FORMATTING_LABELS,
  ORGANIZATION_LABELS,
  TONE_LABELS,
  type GuidanceRule,
} from "@/types/guidance";

/**
 * One rule as one line, in the same terms the renderer uses.
 *
 * Structured kinds (`vocabulary`, `evidence_rule`, `style`) carry a payload
 * rather than prose, and a row that showed `[object Object]` would tell a
 * reader nothing about why the agent behaves as it does. Each payload is parsed
 * through the same shape the backend stores; an unparseable one says so rather
 * than rendering blank, because a rule the user cannot read is a behaviour
 * nobody can explain.
 */
export function ruleSummary(rule: GuidanceRule): string {
  switch (rule.kind) {
    case "rule":
    case "output_rule":
      return rule.content?.trim() || "(empty rule)";

    case "vocabulary": {
      const parsed = VocabularyPayloadSchema.safeParse(rule.payload ?? {});
      if (!parsed.success) return "Unreadable term - this rule will not render";
      const { term, aliases, description } = parsed.data;
      const aliasText = aliases.length ? ` (also: ${aliases.join(", ")})` : "";
      return `"${term}"${aliasText} = ${description}`;
    }

    case "evidence_rule": {
      const parsed = EvidenceRulePayloadSchema.safeParse(rule.payload ?? {});
      if (!parsed.success) return "Unreadable evidence weight - this rule will not render";
      const { evidence_type, weight, source_kind } = parsed.data;
      const label = source_kind ? `${evidence_type} from ${source_kind}` : evidence_type;
      if (weight > 0) return `${label} is strong evidence (weight ${weight})`;
      if (weight < 0) return `${label} alone is not sufficient (weight ${weight})`;
      return `${label} carries no preference (weight 0 - not rendered)`;
    }

    case "style": {
      const parsed = StylePreferenceSchema.safeParse(rule.payload ?? {});
      if (!parsed.success) return "Unreadable style overlay - this rule will not render";
      return describeStyle(parsed.data) || "(empty style overlay)";
    }
  }
}

/** The style overlay as a comma-separated sentence. Empty when nothing is set. */
export function describeStyle(style: {
  audience: keyof typeof AUDIENCE_LABELS | null;
  tone: keyof typeof TONE_LABELS | null;
  technical_depth: keyof typeof DEPTH_LABELS | null;
  output_organization: keyof typeof ORGANIZATION_LABELS | null;
  formatting: (keyof typeof FORMATTING_LABELS)[];
  communication: (keyof typeof COMMUNICATION_LABELS)[];
}): string {
  const parts: string[] = [];
  if (style.audience) parts.push(`audience ${AUDIENCE_LABELS[style.audience]}`);
  if (style.tone) parts.push(`tone ${TONE_LABELS[style.tone]}`);
  if (style.technical_depth) parts.push(`depth ${DEPTH_LABELS[style.technical_depth]}`);
  if (style.output_organization) {
    parts.push(`organization ${ORGANIZATION_LABELS[style.output_organization]}`);
  }
  for (const option of style.formatting) parts.push(FORMATTING_LABELS[option]);
  for (const option of style.communication) parts.push(COMMUNICATION_LABELS[option]);
  return parts.join(", ");
}

/** True when the rule was written back from a conversation rather than typed. */
export function isLearned(rule: GuidanceRule): boolean {
  return rule.source.startsWith("learned:");
}
