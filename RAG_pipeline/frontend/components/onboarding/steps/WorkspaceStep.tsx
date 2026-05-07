"use client";

import { useState } from "react";
import { Building2 } from "lucide-react";
import { useSaveWorkspace } from "@/hooks/useOnboarding";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardBody, CardFooter, CardHeader } from "@/components/ui/card";

interface Props {
  initial: string | null | undefined;
  suggested?: string;
  onBack: () => void;
  onNext: () => void;
}

export function WorkspaceStep({ initial, suggested, onBack, onNext }: Props) {
  const [name, setName] = useState(initial ?? suggested ?? "");
  const [error, setError] = useState<string | null>(null);
  const save = useSaveWorkspace();

  async function handleNext() {
    const trimmed = name.trim();
    if (!trimmed) {
      setError("Please enter a name for your workspace.");
      return;
    }
    setError(null);
    try {
      await save.mutateAsync(trimmed);
      onNext();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save workspace name.");
    }
  }

  return (
    <div className="w-full max-w-xl mx-auto px-6">
      <Card>
        <CardHeader>
          <div className="flex items-center gap-3">
            <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <Building2 className="h-5 w-5" aria-hidden />
            </span>
            <div>
              <h2 className="text-xl font-semibold text-foreground">
                Name your workspace
              </h2>
              <p className="mt-0.5 text-sm text-muted-foreground">
                The company or team this environment belongs to.
              </p>
            </div>
          </div>
        </CardHeader>

        <CardBody className="space-y-2">
          <Label htmlFor="workspace-name">Workspace name</Label>
          <Input
            id="workspace-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={200}
            placeholder="Acme Corp"
            autoFocus
            hasError={Boolean(error)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !save.isPending) handleNext();
            }}
          />
          <p className="text-xs text-muted-foreground">
            You can change this later in settings.
          </p>
          {error && (
            <p className="text-sm text-destructive mt-1">{error}</p>
          )}
        </CardBody>

        <CardFooter>
          <Button variant="ghost" size="md" onClick={onBack}>
            Back
          </Button>
          <Button
            size="md"
            onClick={handleNext}
            isLoading={save.isPending}
          >
            Continue
          </Button>
        </CardFooter>
      </Card>
    </div>
  );
}
