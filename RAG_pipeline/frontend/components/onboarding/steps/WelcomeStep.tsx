"use client";

import { ArrowRight, Check } from "lucide-react";
import { Button } from "@/components/ui/button";

interface Props {
  username: string;
  onNext: () => void;
}

const HIGHLIGHTS = [
  "Name your workspace",
  "Pick the integration platforms you run",
  "Choose the tools your team works in",
  "Connect a codebase — or skip it for now",
  "Take a quick product tour",
];

export function WelcomeStep({ username, onNext }: Props) {
  return (
    <div className="flex flex-col items-center justify-center text-center px-6 max-w-xl mx-auto">
      {/* Soft brand halo for a premium first impression */}
      <div className="relative mb-10">
        <div
          className="absolute inset-0 -inset-x-8 rounded-full bg-primary/10 blur-3xl scale-125"
          aria-hidden
        />
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src="/intodata-logo-hero.png"
          alt="Intodata"
          className="relative h-11 w-auto dark:brightness-0 dark:invert"
        />
      </div>

      <h1 className="text-4xl font-semibold tracking-tight text-foreground mb-3">
        Welcome, {username}
      </h1>
      <p className="text-base text-muted-foreground max-w-md mb-10 leading-relaxed">
        Let&rsquo;s set up your workspace in a couple of quick steps so you can
        start querying your integrations with natural language.
      </p>

      <ul className="w-full max-w-sm text-left space-y-2.5 mb-12">
        {HIGHLIGHTS.map((text) => (
          <li
            key={text}
            className="flex items-start gap-2.5 text-sm text-foreground/80"
          >
            <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
              <Check className="h-2.5 w-2.5" strokeWidth={3} />
            </span>
            {text}
          </li>
        ))}
      </ul>

      <Button size="lg" onClick={onNext}>
        Get started
        <ArrowRight className="h-4 w-4" />
      </Button>
      <p className="mt-4 text-xs text-muted-foreground">
        Takes about 2 minutes.
      </p>
    </div>
  );
}
