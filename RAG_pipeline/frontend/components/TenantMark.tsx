"use client";

import { Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";
import { useTenantContext } from "@/contexts/TenantContext";

type Size = "sm" | "md" | "lg";

interface Props {
  /** Only render the icon mark — used in the collapsed sidebar */
  iconOnly?: boolean;
  /** Show the small environment pill when not production. Default true. */
  showEnvironment?: boolean;
  /** Optional secondary label (e.g. per-user workspace override) */
  subtitle?: string | null;
  size?: Size;
  className?: string;
}

const iconSizes: Record<Size, string> = {
  sm: "h-6 w-6 rounded-md",
  md: "h-7 w-7 rounded-md",
  lg: "h-10 w-10 rounded-lg",
};

const iconGlyphSizes: Record<Size, string> = {
  sm: "h-3.5 w-3.5",
  md: "h-4 w-4",
  lg: "h-5 w-5",
};

const nameSizes: Record<Size, string> = {
  sm: "text-sm",
  md: "text-sm",
  lg: "text-base",
};

export function TenantMark({
  iconOnly = false,
  showEnvironment = true,
  subtitle,
  size = "md",
  className,
}: Props) {
  const { tenant, isProduction } = useTenantContext();

  const mark = (
    <span
      className={cn(
        "shrink-0 flex items-center justify-center bg-primary text-primary-foreground shadow-elevation-xs",
        iconSizes[size],
      )}
      aria-hidden
    >
      <Sparkles className={iconGlyphSizes[size]} />
    </span>
  );

  if (iconOnly) {
    return (
      <div
        className={cn("flex items-center justify-center", className)}
        title={tenant.name}
      >
        {mark}
      </div>
    );
  }

  return (
    <div className={cn("flex items-center gap-2.5", className)}>
      {mark}
      <div className="flex items-center gap-2 min-w-0">
        <span
          className={cn(
            "font-semibold tracking-tight text-foreground truncate",
            nameSizes[size],
          )}
        >
          {tenant.name}
        </span>
        {subtitle && (
          <span className="hidden sm:inline text-xs text-muted-foreground truncate">
            · {subtitle}
          </span>
        )}
        {showEnvironment && !isProduction && (
          <EnvironmentBadge
            environment={tenant.environment}
            className="shrink-0"
          />
        )}
      </div>
    </div>
  );
}

const ENV_SHORT: Record<string, string> = {
  development: "Dev",
  staging: "Stage",
  preview: "Preview",
  production: "Prod",
};

export function EnvironmentBadge({
  environment,
  className,
}: {
  environment: string;
  className?: string;
}) {
  const env = environment.toLowerCase();
  const palette =
    env === "staging"
      ? "bg-amber-100 text-amber-800 border-amber-200"
      : env === "preview"
        ? "bg-violet-100 text-violet-800 border-violet-200"
        : "bg-muted text-muted-foreground border-border";
  const label = ENV_SHORT[env] ?? environment;
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide",
        palette,
        className,
      )}
      title={environment}
    >
      <span
        className={cn(
          "mr-1 inline-block h-1.5 w-1.5 rounded-full",
          env === "staging" && "bg-amber-500",
          env === "preview" && "bg-violet-500",
          env !== "staging" && env !== "preview" && "bg-muted-foreground/60",
        )}
        aria-hidden
      />
      {label}
    </span>
  );
}
