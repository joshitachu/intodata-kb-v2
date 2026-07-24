import type { ConversationDetailResponse } from "@/types";

type AgentPlan = NonNullable<ConversationDetailResponse["agent_plan"]>;

export function AgentPlanCard({ plan }: { plan: AgentPlan }) {
  return (
    <section aria-label="Agent plan" className="mx-auto mb-3 w-full max-w-3xl rounded-lg border bg-muted/30 p-3 text-sm">
      <div className="mb-1 flex items-center justify-between"><strong>Plan</strong><span className="text-muted-foreground">{plan.status} · v{plan.version}</span></div>
      <p className="font-medium">{plan.goal}</p>
      {plan.success_criteria.length > 0 && <p className="mt-1 text-muted-foreground">Success: {plan.success_criteria.join(" · ")}</p>}
      <ol className="mt-2 list-decimal space-y-1 pl-5">
        {plan.steps.map((step) => <li key={step.key}>{step.objective}{step.depends_on.length > 0 && <span className="text-muted-foreground"> (after {step.depends_on.join(", ")})</span>}</li>)}
      </ol>
    </section>
  );
}
