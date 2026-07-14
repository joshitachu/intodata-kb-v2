import {
  AlertTriangle,
  CheckCircle2,
  CircleHelp,
  ExternalLink,
  Loader2,
  XCircle,
} from "lucide-react";
import type { StructuredAnswer as StructuredAnswerValue } from "@/types";
import { cn } from "@/lib/utils";
import { MarkdownContent } from "./MarkdownContent";

interface StructuredAnswerProps {
  answer: StructuredAnswerValue;
}

const STATUS_STYLE = {
  completed: "text-emerald-700 dark:text-emerald-400",
  complete: "text-emerald-700 dark:text-emerald-400",
  healthy: "text-emerald-700 dark:text-emerald-400",
  partial: "text-amber-700 dark:text-amber-400",
  degraded: "text-amber-700 dark:text-amber-400",
  failed: "text-red-700 dark:text-red-400",
  insufficient: "text-amber-700 dark:text-amber-400",
  cancelled: "text-muted-foreground",
  running: "text-blue-700 dark:text-blue-400",
  unknown: "text-muted-foreground",
} as const;

function StatusIcon({ value }: { value: string }) {
  if (value === "running") return <Loader2 className="h-3.5 w-3.5 animate-spin" />;
  if (["completed", "complete", "healthy"].includes(value)) {
    return <CheckCircle2 className="h-3.5 w-3.5" />;
  }
  if (["partial", "degraded", "insufficient"].includes(value)) {
    return <AlertTriangle className="h-3.5 w-3.5" />;
  }
  if (value === "failed") {
    return <XCircle className="h-3.5 w-3.5" />;
  }
  return <CircleHelp className="h-3.5 w-3.5" />;
}

function StatusFact({ label, value }: { label: string; value: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 text-xs",
        STATUS_STYLE[value as keyof typeof STATUS_STYLE] ?? "text-muted-foreground",
      )}
    >
      <StatusIcon value={value} />
      <span className="text-muted-foreground">{label}:</span>
      <span className="font-medium capitalize">{value}</span>
    </span>
  );
}

function AnswerTable({ rows = [] }: { rows?: Array<Record<string, unknown>> }) {
  if (rows.length === 0) return null;
  const columns = Array.from(new Set(rows.flatMap((row) => Object.keys(row))));
  return (
    <div
      className="max-w-full overflow-x-auto overscroll-x-contain border-y"
      role="region"
      aria-label="Answer table"
      tabIndex={0}
      data-testid="answer-table-scroll"
    >
      <table className="w-full min-w-[36rem] table-auto text-left text-sm">
        <thead className="bg-muted/40 text-xs text-muted-foreground">
          <tr>
            {columns.map((column) => (
              <th key={column} className="px-3 py-2 font-medium capitalize">
                {column.replaceAll("_", " ")}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y">
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex} className="align-top">
              {columns.map((column) => (
                <td
                  key={column}
                  className="max-w-[28rem] whitespace-pre-wrap break-words px-3 py-2.5 leading-5"
                >
                  {String(row[column] ?? "")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function StructuredAnswer({ answer }: StructuredAnswerProps) {
  const summaryRepeated = answer.sections.some(
    (section) => section.body?.trim().startsWith(answer.summary.trim()),
  );

  return (
    <article className="min-w-0 space-y-4 break-words" data-testid="structured-answer">
      <header className="space-y-2">
        <h2 className="text-lg font-semibold leading-7">{answer.headline}</h2>
        {!summaryRepeated && (
          <p className="text-sm leading-6 text-foreground/90">{answer.summary}</p>
        )}
        <div className="flex flex-wrap gap-x-4 gap-y-1 border-b pb-3">
          <StatusFact label="Execution" value={answer.execution_status} />
          <StatusFact label="Answer" value={answer.answer_status} />
          <StatusFact label="Observed state" value={answer.domain_status} />
        </div>
      </header>

      {answer.sections.map((section, index) => (
        <section key={`${section.heading}-${index}`} className="min-w-0 space-y-2">
          <h3 className="text-sm font-semibold">{section.heading}</h3>
          {section.body && <MarkdownContent content={section.body} />}
          <AnswerTable rows={section.rows} />
        </section>
      ))}

      {answer.limitations.length > 0 && (
        <section className="border-l-2 border-amber-500/60 pl-3 text-sm">
          <h3 className="font-medium">Limitations</h3>
          <ul className="mt-1 space-y-1 text-muted-foreground">
            {answer.limitations.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>
      )}

      {answer.citations.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <span className="font-medium text-foreground">Evidence</span>
          {answer.citations.map((citation, index) =>
            citation.uri ? (
              <a
                key={citation.evidence_id}
                href={citation.uri}
                className="inline-flex min-w-0 max-w-full items-center gap-1 break-all hover:text-foreground"
                target="_blank"
                rel="noopener noreferrer"
              >
                [{index + 1}] {citation.label}
                <ExternalLink className="h-3 w-3 shrink-0" />
              </a>
            ) : (
              <span key={citation.evidence_id} className="min-w-0 max-w-full break-all">
                [{index + 1}] {citation.label}
              </span>
            ),
          )}
        </div>
      )}

      {answer.artifacts.length > 0 && (
        <div className="flex flex-wrap gap-2 border-t pt-3 text-sm">
          {answer.artifacts.map((artifact) => (
            <a
              key={`${artifact.url}-${artifact.label}`}
              href={artifact.url}
              className="inline-flex min-w-0 max-w-full items-center gap-1 break-all font-medium text-primary hover:underline"
              target="_blank"
              rel="noopener noreferrer"
            >
              {artifact.label}
              <ExternalLink className="h-3.5 w-3.5" />
            </a>
          ))}
        </div>
      )}
    </article>
  );
}
