"use client";

import { Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardFooter, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { CompanyStatus } from "@/types/onboarding";

interface Props {
  username: string;
  companyStatus: CompanyStatus | null | undefined;
  onNext: () => void;
}

const PLATFORM_LABELS: Record<string, string> = {
  talend: "Talend",
  workato: "Workato",
  lobster: "Lobster",
  generic: "Generic codebase",
};

export function UserWelcomeStep({ username, companyStatus, onNext }: Props) {
  const workspaceName = companyStatus?.workspace_name;
  const platforms = companyStatus?.selected_platforms ?? [];

  return (
    <div className="w-full max-w-xl mx-auto px-6">
      <Card>
        <CardHeader>
          <div className="flex items-center gap-3">
            <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <Sparkles className="h-5 w-5" aria-hidden />
            </span>
            <div>
              <h2 className="text-xl font-semibold text-foreground">
                Welcome{workspaceName ? ` to ${workspaceName}` : ""}, {username}
              </h2>
              <p className="mt-0.5 text-sm text-muted-foreground">
                Your workspace is ready to use.
              </p>
            </div>
          </div>
        </CardHeader>

        <CardBody className="space-y-4">
          {platforms.length > 0 && (
            <div>
              <p className="text-sm font-medium text-foreground mb-2">
                Configured integrations
              </p>
              <div className="flex flex-wrap gap-2">
                {platforms.map((id) => (
                  <Badge key={id} variant="muted">
                    {PLATFORM_LABELS[id] ?? id}
                  </Badge>
                ))}
              </div>
            </div>
          )}
          <p className="text-sm text-muted-foreground">
            Your admin has already set up the workspace. You can start querying
            your integration knowledge base right away.
          </p>
        </CardBody>

        <CardFooter>
          <div />
          <Button size="md" onClick={onNext}>
            Let&rsquo;s go
          </Button>
        </CardFooter>
      </Card>
    </div>
  );
}
