"use client";

import { forwardRef, type ButtonHTMLAttributes } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

type Variant = "primary" | "secondary" | "ghost" | "destructive";
type Size = "sm" | "md" | "lg";

const base =
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md font-medium " +
  "transition-[background-color,border-color,box-shadow,color] focus-visible:outline-none " +
  "focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:ring-offset-2 " +
  "focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-50";

const variants: Record<Variant, string> = {
  primary:
    "bg-primary text-primary-foreground shadow-elevation-xs hover:bg-primary/90 " +
    "active:bg-primary/95",
  secondary:
    "border border-border bg-background text-foreground hover:bg-muted/60 " +
    "hover:border-border/80 shadow-elevation-xs",
  ghost: "text-muted-foreground hover:text-foreground hover:bg-muted/60",
  destructive:
    "bg-destructive text-destructive-foreground shadow-elevation-xs hover:bg-destructive/90",
};

const sizes: Record<Size, string> = {
  sm: "h-8 px-3 text-xs",
  md: "h-9 px-4 text-sm",
  lg: "h-11 px-6 text-sm",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  isLoading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      className,
      variant = "primary",
      size = "md",
      isLoading = false,
      disabled,
      children,
      type = "button",
      ...rest
    },
    ref,
  ) => {
    return (
      <button
        ref={ref}
        type={type}
        disabled={disabled || isLoading}
        className={cn(base, variants[variant], sizes[size], className)}
        {...rest}
      >
        {isLoading && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
        {children}
      </button>
    );
  },
);
Button.displayName = "Button";
