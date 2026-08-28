"use client";

import { ArrowLeft, ArrowRight, Check, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { Persona } from "@/types/onboarding";
import { profileFor } from "../personaContent";

interface Props {
  persona: Persona;
  onBack: () => void;
  onNext: () => void;
}

/**
 * "Here is your authority, and here is where it stops."
 *
 * Stating the boundary during onboarding is the point of this screen. Left
 * unsaid, it becomes a support question later ("why can't I see project X?"),
 * and a member who does not know their own scope reads a missing project as a
 * broken product.
 */
export function CapabilityStep({ persona, onBack, onNext }: Props) {
  const profile = profileFor(persona);

  return (
    <div className="animate-step-enter mx-auto w-full max-w-2xl px-6">
      <div className="mb-8">
        <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-primary">
          Your access
        </p>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">
          What you can do as {profile.label.toLowerCase() === "member" ? "a member" : `an ${profile.label.toLowerCase()}`}
        </h1>
      </div>

      <ul className="space-y-3">
        {profile.can.map((capability) => (
          <li
            key={capability.title}
            className="flex gap-3 rounded-xl border border-border bg-card p-4"
          >
            <span
              className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary"
              aria-hidden
            >
              <Check className="h-3 w-3" strokeWidth={3} />
            </span>
            <div>
              <p className="text-sm font-semibold text-foreground">
                {capability.title}
              </p>
              <p className="mt-1 text-sm leading-6 text-muted-foreground">
                {capability.detail}
              </p>
            </div>
          </li>
        ))}
      </ul>

      <div className="mt-5 flex gap-3 rounded-xl border border-amber-300/60 bg-amber-50/60 p-4 dark:border-amber-500/25 dark:bg-amber-500/[0.07]">
        <ShieldCheck
          className="mt-0.5 h-4 w-4 shrink-0 text-amber-700 dark:text-amber-400"
          aria-hidden
        />
        <div>
          <p className="text-sm font-semibold text-foreground">
            Where your access stops
          </p>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">
            {profile.boundary}
          </p>
        </div>
      </div>

      <div className="mt-8 flex items-center justify-between">
        <Button variant="ghost" size="md" onClick={onBack}>
          <ArrowLeft className="h-4 w-4" aria-hidden />
          Back
        </Button>
        <Button size="md" onClick={onNext}>
          Next
          <ArrowRight className="h-4 w-4" aria-hidden />
        </Button>
      </div>
    </div>
  );
}
