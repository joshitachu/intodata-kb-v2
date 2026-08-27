import React from "react";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  statusLoading: true,
  projectsLoading: true,
}));

vi.mock("@/hooks/useOnboarding", () => ({
  useOnboardingStatus: () => ({
    data: { workspace_name: "Test workspace", selected_platforms: ["talend"] },
    isLoading: state.statusLoading,
  }),
  useOnboardingConfig: () => ({
    data: { platforms: [{ id: "talend", label: "Talend" }] },
  }),
}));

vi.mock("@tanstack/react-query", async () => {
  const ReactModule = await import("react");
  return {
    useQuery: ({ queryKey }: { queryKey: string[] }) => {
      // Keep this a real hook. The four queries inside useProjectLiveSources
      // must contribute to SourcesSettingsPage's hook count for this test to
      // detect a call placed after the loading early-return.
      ReactModule.useState(null);
      if (queryKey[0] === "source-projects") {
        return {
          data: state.projectsLoading ? undefined : { projects: [], total: 0 },
          isLoading: state.projectsLoading,
        };
      }
      return { data: undefined, isLoading: false };
    },
    useQueryClient: () => ({ invalidateQueries: vi.fn() }),
  };
});

vi.mock("@/app/settings/sources/AzureDevOpsConnectPanel", () => ({
  AzureDevOpsConnectPanel: () => null,
}));
vi.mock("@/app/settings/sources/GithubAppConnectPanel", () => ({
  GithubAppConnectPanel: () => null,
}));
vi.mock("@/app/settings/sources/MultiRepoMembersPanel", () => ({
  MultiRepoMembersPanel: () => null,
}));
vi.mock("@/app/settings/sources/ProjectConnectorBindings", () => ({
  ProjectConnectorBindings: () => null,
}));
vi.mock("@/app/settings/sources/SimpleConnectorPanel", () => ({
  SimpleConnectorPanel: () => null,
}));
vi.mock("@/app/settings/sources/GeneratePackDialog", () => ({
  GeneratePackDialog: () => null,
  applyGenerateChoice: vi.fn(),
}));

import SourcesSettingsPage from "@/app/settings/sources/page";

describe("SourcesSettingsPage", () => {
  beforeEach(() => {
    state.statusLoading = true;
    state.projectsLoading = true;
  });

  it("keeps a stable hook order when loading finishes", () => {
    const view = render(<SourcesSettingsPage />);
    expect(screen.getByText("Loading sources…")).toBeInTheDocument();

    state.statusLoading = false;
    state.projectsLoading = false;

    expect(() => view.rerender(<SourcesSettingsPage />)).not.toThrow();
    expect(screen.getByRole("heading", { name: "Sources" })).toBeInTheDocument();
  });
});
