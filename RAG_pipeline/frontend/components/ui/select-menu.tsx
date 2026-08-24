"use client";

import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { Check, ChevronDown, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * A styled single-select dropdown.
 *
 * Native `<select>` cannot be styled beyond its trigger — the option list is
 * rendered by the OS, so long labels blow out the trigger width and the popup
 * ignores the app theme entirely. This renders both halves ourselves so every
 * project/status picker in the app looks the same in light and dark mode.
 */

export interface SelectMenuOption {
  value: string;
  label: string;
  /** Secondary line under the label inside the menu (not shown on the trigger). */
  hint?: string;
}

export interface SelectMenuProps {
  value: string;
  onChange: (value: string) => void;
  options: SelectMenuOption[];
  /** Trigger label when `value` is empty. */
  placeholder?: string;
  /** Optional leading icon on the trigger. */
  icon?: LucideIcon;
  /** Required — the trigger renders a label, not a form control. */
  ariaLabel: string;
  align?: "start" | "end";
  size?: "sm" | "md";
  /** Visual treatment: `pill` for toolbars, `field` for forms. */
  variant?: "pill" | "field";
  disabled?: boolean;
  className?: string;
  /** Menu width; defaults to matching the trigger with a sane minimum. */
  contentClassName?: string;
}

const TRIGGER_SIZES = {
  sm: "h-7 gap-1.5 px-2.5 text-xs",
  md: "h-9 gap-2 px-3 text-sm",
} as const;

const TRIGGER_VARIANTS = {
  pill: "rounded-full border border-border bg-muted/40 hover:bg-muted",
  field: "rounded-md border border-border bg-background shadow-elevation-xs hover:bg-muted/40",
} as const;

export function SelectMenu({
  value,
  onChange,
  options,
  placeholder = "Select...",
  icon: Icon,
  ariaLabel,
  align = "start",
  size = "sm",
  variant = "pill",
  disabled = false,
  className,
  contentClassName,
}: SelectMenuProps) {
  const selected = options.find((option) => option.value === value);

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild disabled={disabled}>
        <button
          type="button"
          aria-label={ariaLabel}
          className={cn(
            "inline-flex max-w-[14rem] items-center justify-between overflow-hidden",
            "font-medium transition-colors",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
            "disabled:cursor-not-allowed disabled:opacity-50",
            "data-[state=open]:bg-muted data-[state=open]:text-foreground",
            selected ? "text-foreground" : "text-muted-foreground",
            TRIGGER_SIZES[size],
            TRIGGER_VARIANTS[variant],
            className,
          )}
        >
          {Icon && <Icon className="h-3.5 w-3.5 shrink-0 opacity-70" aria-hidden />}
          <span className="truncate">{selected?.label ?? placeholder}</span>
          <ChevronDown className="h-3.5 w-3.5 shrink-0 opacity-60" aria-hidden />
        </button>
      </DropdownMenu.Trigger>

      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align={align}
          sideOffset={6}
          collisionPadding={12}
          className={cn(
            "z-50 max-h-[min(20rem,var(--radix-dropdown-menu-content-available-height))]",
            "min-w-[var(--radix-dropdown-menu-trigger-width)] max-w-[22rem] overflow-y-auto",
            "rounded-xl border border-border bg-card p-1 text-card-foreground shadow-elevation-lg",
            contentClassName,
          )}
        >
          {options.map((option) => {
            const isSelected = option.value === value;
            return (
              <DropdownMenu.Item
                key={option.value || "__empty__"}
                onSelect={() => onChange(option.value)}
                className={cn(
                  "flex cursor-pointer select-none items-start gap-2 rounded-lg px-2.5 py-2",
                  "text-sm outline-none transition-colors",
                  "data-[highlighted]:bg-muted data-[highlighted]:text-foreground",
                  isSelected ? "text-foreground" : "text-muted-foreground",
                )}
              >
                <Check
                  className={cn(
                    "mt-0.5 h-3.5 w-3.5 shrink-0 text-primary",
                    isSelected ? "opacity-100" : "opacity-0",
                  )}
                  aria-hidden
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">{option.label}</span>
                  {option.hint && (
                    <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                      {option.hint}
                    </span>
                  )}
                </span>
              </DropdownMenu.Item>
            );
          })}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
