"use client";

import { forwardRef, type InputHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  hasError?: boolean;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ className, hasError = false, type = "text", ...rest }, ref) => {
    return (
      <input
        ref={ref}
        type={type}
        className={cn(
          "w-full h-10 rounded-md border bg-background px-3 text-sm text-foreground",
          "placeholder:text-muted-foreground/70",
          "shadow-elevation-xs",
          "transition-[border-color,box-shadow]",
          "focus:outline-none focus:border-primary focus:ring-2 focus:ring-primary/20",
          "disabled:opacity-50 disabled:cursor-not-allowed",
          hasError
            ? "border-destructive/60 focus:border-destructive focus:ring-destructive/20"
            : "border-border",
          className,
        )}
        {...rest}
      />
    );
  },
);
Input.displayName = "Input";
