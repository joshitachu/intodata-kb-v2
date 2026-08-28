"use client";

import { ArrowRight, Quote, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import type { InviteContext, Persona } from "@/types/onboarding";
import { profileFor } from "../personaContent";

interface Props {
  username: string;
  workspaceName: string | null | undefined;
  persona: Persona;
  invite: InviteContext | null | undefined;
  onNext: () => void;
}

/**
 * Opening screen of the member walkthrough.
 *
 * Deliberately names the person who approved the request and echoes their note.
 * The previous welcome greeted every new member with the same anonymous "your
 * workspace is ready", which reads as automated mail rather than someone
 * deciding to give them access.
 */
export function MemberWelcomeStep({
  username,
  workspaceName,
  persona,
  invite,
  onNext,
}: Props) {
  const profile = profileFor(persona);
  const approver = invite?.approved_by;
  const note = invite?.welcome_note;
  const projects = invite?.assigned_project_ids ?? [];

  return (
    <div className="animate-step-enter mx-auto w-full max-w-xl px-6">
      <div className="mb-8 flex flex-col items-center text-center">
        <div className="relative mb-7">
          <span
            className="absolute inset-0 scale-125 rounded-full bg-primary/20 blur-3xl"
            aria-hidden
          />
          <div className="relative flex h-16 w-16 items-center justify-center rounded-2xl bg-primary text-primary-foreground shadow-elevation-lg ring-8 ring-primary/10">
            <Sparkles className="h-7 w-7" aria-hidden />
          </div>
        </div>

        <Badge variant="muted">{profile.label}</Badge>
        <h1 className="mt-4 text-3xl font-semibold tracking-tight text-foreground">
          Welcome{workspaceName ? ` to ${workspaceName}` : ""}, {username}
        </h1>
        <p className="mt-3 max-w-md text-base leading-relaxed text-muted-foreground">
          {profile.mission}
        </p>
      </div>

      {approver && (
        <div className="mb-6 rounded-xl border border-border bg-muted/30 p-4">
          <p className="text-sm text-foreground">
            <span className="font-medium">{approver}</span> approved your access
            {projects.length > 0 && (
              <>
                {" "}
                for{" "}
                <span className="font-medium">
                  {projects.length === 1
                    ? projects[0]
                    : `${projects.length} projects`}
                </span>
              </>
            )}
            .
          </p>
          {note && (
            <p className="mt-3 flex gap-2 border-l-2 border-primary/40 pl-3 text-sm italic leading-6 text-muted-foreground">
              <Quote className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary/60" aria-hidden />
              {note}
            </p>
          )}
        </div>
      )}

      <div className="flex justify-center">
        <Button size="lg" onClick={onNext}>
          Show me around
          <ArrowRight className="h-4 w-4" aria-hidden />
        </Button>
      </div>
      <p className="mt-4 text-center text-xs text-muted-foreground">
        Three short screens, then you are in.
      </p>
    </div>
  );
}
