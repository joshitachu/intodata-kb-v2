"use client";

import { forwardRef, type HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

type Variant = "default" | "muted" | "success" | "warning" | "soon" | "destructive";

const variants: Record<Variant, string> = {
  default: "bg-primary/10 text-primary border border-primary/20",
  muted: "bg-muted text-muted-foreground border border-border",
  success: "bg-emerald-50 text-emerald-700 border border-emerald-200",
  warning: "bg-amber-50 text-amber-800 border border-amber-200",
  soon: "bg-muted/80 text-muted-foreground border border-border/80 tracking-wide uppercase",
  destructive: "bg-destructive/10 text-destructive border border-destructive/20",
};

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: Variant;
}

export const Badge = forwardRef<HTMLSpanElement, BadgeProps>(
  ({ className, variant = "default", ...rest }, ref) => (
    <span
      ref={ref}
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium",
        variants[variant],
        className,
      )}
      {...rest}
    />
  ),
);
Badge.displayName = "Badge";
