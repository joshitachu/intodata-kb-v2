"use client";

import { useEffect, type ReactNode } from "react";
import { AlertCircle, CheckCircle2, Loader2, X, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Presentation primitives shared by the Organization dashboard panels.
 *
 * The four panels used to be four standalone pages, each with its own table,
 * modal and alert markup hardcoded against `bg-white`/`text-gray-*`. Everything
 * here is theme-token based so the dashboard renders correctly in dark mode.
 */

/* ── Panel shell ─────────────────────────────────────────────── */

export function PanelHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0">
        <h2 className="text-base font-semibold text-foreground">{title}</h2>
        <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}

/* ── Feedback ────────────────────────────────────────────────── */

export function Alert({
  tone,
  children,
  onDismiss,
}: {
  tone: "error" | "success" | "info";
  children: ReactNode;
  onDismiss?: () => void;
}) {
  const tones = {
    error: "border-destructive/30 bg-destructive/5 text-destructive",
    success: "border-emerald-200 bg-emerald-50 text-emerald-700",
    info: "border-border bg-muted/50 text-foreground",
  } as const;

  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={cn(
        "flex items-start gap-2 rounded-lg border px-3 py-2.5 text-sm",
        tones[tone],
      )}
    >
      {tone === "error" ? (
        <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
      ) : tone === "success" ? (
        <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
      ) : null}
      <span className="min-w-0 flex-1 break-words">{children}</span>
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss"
          className="shrink-0 opacity-60 transition-opacity hover:opacity-100"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
}

/** Auto-clearing success message. Returns nothing when `message` is empty. */
export function Toast({
  message,
  onClear,
}: {
  message: string;
  onClear: () => void;
}) {
  useEffect(() => {
    if (!message) return;
    const timer = setTimeout(onClear, 4000);
    return () => clearTimeout(timer);
  }, [message, onClear]);

  if (!message) return null;
  return <Alert tone="success">{message}</Alert>;
}

export function PanelLoading({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2 px-1 py-8 text-sm text-muted-foreground">
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
      {label}
    </div>
  );
}

/* ── Table ───────────────────────────────────────────────────── */

export function DataTable({
  columns,
  children,
  empty,
  isEmpty,
}: {
  columns: string[];
  children: ReactNode;
  empty: string;
  isEmpty: boolean;
}) {
  return (
    <div
      // Only one organization panel is mounted at a time, so this resolves to
      // the table of whichever tab is open. The walkthrough scopes it by tab
      // (`[data-tour="admin-access"] [data-tour="admin-org-table"]`) rather
      // than spotlighting the whole panel.
      data-tour="admin-org-table"
      className="overflow-x-auto rounded-xl border border-border bg-card shadow-elevation-sm"
    >
      <table className="w-full min-w-[42rem] text-sm">
        <thead>
          <tr className="border-b border-border bg-muted/40">
            {columns.map((column) => (
              <th
                key={column}
                scope="col"
                className="whitespace-nowrap px-4 py-2.5 text-left text-xs font-medium uppercase tracking-wide text-muted-foreground"
              >
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {isEmpty ? (
            <tr>
              <td
                colSpan={columns.length}
                className="px-4 py-10 text-center text-sm text-muted-foreground"
              >
                {empty}
              </td>
            </tr>
          ) : (
            children
          )}
        </tbody>
      </table>
    </div>
  );
}

export function Row({ children }: { children: ReactNode }) {
  return <tr className="transition-colors hover:bg-muted/30">{children}</tr>;
}

export function Cell({
  children,
  className,
  muted = false,
}: {
  children: ReactNode;
  className?: string;
  muted?: boolean;
}) {
  return (
    <td
      className={cn(
        "px-4 py-3 align-middle",
        muted && "text-muted-foreground",
        className,
      )}
    >
      {children}
    </td>
  );
}

/** Right-aligned inline actions cell. */
export function ActionCell({ children }: { children: ReactNode }) {
  return (
    <td className="px-4 py-3 align-middle">
      <div className="flex items-center justify-end gap-1">{children}</div>
    </td>
  );
}

export function InlineAction({
  onClick,
  disabled,
  title,
  tone = "default",
  children,
}: {
  onClick: () => void;
  disabled?: boolean;
  title?: string;
  tone?: "default" | "danger" | "success";
  children: ReactNode;
}) {
  const tones = {
    default: "text-muted-foreground hover:text-foreground hover:bg-muted",
    danger: "text-destructive hover:bg-destructive/10",
    success: "text-emerald-600 hover:bg-emerald-50",
  } as const;

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={cn(
        "rounded-md px-2 py-1 text-xs font-medium transition-colors",
        "disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent",
        tones[tone],
      )}
    >
      {children}
    </button>
  );
}

/* ── Modal ───────────────────────────────────────────────────── */

// Re-exported so the panels keep importing it from one place, while the
// implementation is shared with every other admin surface.
export { Modal } from "@/components/ui/modal";

/* ── Permission / role chips ─────────────────────────────────── */

export function Chip({
  children,
  tone = "default",
  title,
}: {
  children: ReactNode;
  tone?: "default" | "muted" | "owner" | "permission";
  title?: string;
}) {
  const tones = {
    default: "border-primary/20 bg-primary/10 text-primary",
    muted: "border-border bg-muted text-muted-foreground",
    owner: "border-amber-200 bg-amber-50 text-amber-800",
    permission: "border-border bg-muted/60 text-muted-foreground font-mono",
  } as const;

  return (
    <span
      title={title}
      className={cn(
        "inline-flex items-center rounded-md border px-1.5 py-0.5 text-[11px] font-medium",
        tones[tone],
      )}
    >
      {children}
    </span>
  );
}

export function StatusDot({ active }: { active: boolean }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs">
      <span
        className={cn(
          "h-1.5 w-1.5 rounded-full",
          active ? "bg-emerald-500" : "bg-muted-foreground/40",
        )}
      />
      {active ? "Active" : "Inactive"}
    </span>
  );
}

/** Checkbox list used for role/permission selection. */
export function CheckboxGrid<T extends { id: number }>({
  items,
  selected,
  onToggle,
  render,
  columns = 2,
}: {
  items: T[];
  selected: number[];
  onToggle: (id: number) => void;
  render: (item: T) => ReactNode;
  columns?: 1 | 2 | 3;
}) {
  const gridClass = { 1: "grid-cols-1", 2: "sm:grid-cols-2", 3: "sm:grid-cols-3" }[columns];
  return (
    <div className={cn("grid grid-cols-1 gap-1.5", gridClass)}>
      {items.map((item) => {
        const isChecked = selected.includes(item.id);
        return (
          <label
            key={item.id}
            className={cn(
              "flex cursor-pointer items-center gap-2 rounded-md border px-2.5 py-1.5 text-sm transition-colors",
              isChecked
                ? "border-primary/40 bg-primary/5 text-foreground"
                : "border-border text-muted-foreground hover:bg-muted/50",
            )}
          >
            <input
              type="checkbox"
              checked={isChecked}
              onChange={() => onToggle(item.id)}
              className="h-3.5 w-3.5 shrink-0 accent-[hsl(var(--primary))]"
            />
            <span className="min-w-0 truncate">{render(item)}</span>
          </label>
        );
      })}
    </div>
  );
}

/* ── Summary tiles ───────────────────────────────────────────── */

export function StatTile({
  label,
  value,
  hint,
  icon: Icon,
  tone = "default",
}: {
  label: string;
  value: string | number;
  hint?: string;
  icon: LucideIcon;
  tone?: "default" | "attention";
}) {
  return (
    <div className="rounded-xl border border-border bg-card p-4 shadow-elevation-sm">
      <div className="flex items-start justify-between gap-3">
        <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {label}
        </span>
        <span
          className={cn(
            "flex h-7 w-7 shrink-0 items-center justify-center rounded-lg",
            tone === "attention"
              ? "bg-amber-50 text-amber-700"
              : "bg-primary/10 text-primary",
          )}
        >
          <Icon className="h-3.5 w-3.5" aria-hidden />
        </span>
      </div>
      <p className="mt-2 text-2xl font-semibold tabular-nums text-foreground">{value}</p>
      {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}
