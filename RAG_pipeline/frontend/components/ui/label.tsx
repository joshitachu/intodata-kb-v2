"use client";

import { forwardRef, type LabelHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export interface LabelProps extends LabelHTMLAttributes<HTMLLabelElement> {
  optional?: boolean;
}

export const Label = forwardRef<HTMLLabelElement, LabelProps>(
  ({ className, children, optional = false, ...rest }, ref) => (
    <label
      ref={ref}
      className={cn(
        "block text-sm font-medium text-foreground mb-1.5",
        className,
      )}
      {...rest}
    >
      {children}
      {optional && (
        <span className="ml-1.5 text-xs font-normal text-muted-foreground">
          (optional)
        </span>
      )}
    </label>
  ),
);
Label.displayName = "Label";
