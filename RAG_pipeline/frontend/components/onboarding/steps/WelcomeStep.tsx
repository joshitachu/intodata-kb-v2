"use client";

import { Sparkles } from "lucide-react";

interface Props {
  username: string;
  onNext: () => void;
}

export function WelcomeStep({ username, onNext }: Props) {
  return (
    <div className="flex flex-col items-center justify-center text-center px-6 py-10 max-w-2xl mx-auto">
      <div className="w-16 h-16 rounded-full bg-primary/10 flex items-center justify-center mb-6">
        <Sparkles className="h-8 w-8 text-primary" />
      </div>
      <h1 className="text-3xl font-semibold mb-3">
        Welcome to IntoData, {username}
      </h1>
      <p className="text-muted-foreground text-base max-w-md mb-8">
        Let&rsquo;s set up your workspace in a couple of quick steps so you can start
        querying your integrations with natural language.
      </p>
      <ul className="text-sm text-muted-foreground space-y-2 mb-10 text-left">
        <li>• Name your workspace</li>
        <li>• Pick the integration platforms your team works with</li>
        <li>• Choose communication &amp; ticketing tools to connect</li>
        <li>• Optionally connect your source codebase</li>
        <li>• Take a short tour of the product</li>
      </ul>
      <button
        onClick={onNext}
        className="px-6 py-2.5 bg-primary text-primary-foreground rounded-md text-sm font-medium hover:bg-primary/90 transition-colors"
      >
        Get started
      </button>
    </div>
  );
}
