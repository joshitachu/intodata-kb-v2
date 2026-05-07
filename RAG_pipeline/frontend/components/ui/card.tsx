"use client";

import { forwardRef, type HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export const Card = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...rest }, ref) => (
    <div
      ref={ref}
      className={cn(
        "rounded-xl border border-border bg-card text-card-foreground",
        "shadow-elevation-sm",
        className,
      )}
      {...rest}
    />
  ),
);
Card.displayName = "Card";

export const CardHeader = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...rest }, ref) => (
    <div
      ref={ref}
      className={cn("px-6 pt-6 pb-4 border-b border-border/60", className)}
      {...rest}
    />
  ),
);
CardHeader.displayName = "CardHeader";

export const CardBody = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...rest }, ref) => (
    <div ref={ref} className={cn("px-6 py-5", className)} {...rest} />
  ),
);
CardBody.displayName = "CardBody";

export const CardFooter = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...rest }, ref) => (
    <div
      ref={ref}
      className={cn(
        "px-6 py-4 border-t border-border/60 flex items-center justify-between gap-3",
        className,
      )}
      {...rest}
    />
  ),
);
CardFooter.displayName = "CardFooter";
