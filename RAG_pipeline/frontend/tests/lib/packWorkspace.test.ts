import { describe, expect, it } from "vitest";
import { closePackWorkspaceHref, packWorkspaceHref, stagePackCreateCommand, takePackCreateCommand } from "@/lib/packWorkspace";

describe("Pack workbench URLs", () => {
  it("opens the Pack workbench without losing the active chat or project context", () => {
    expect(packWorkspaceHref({ conversationId: 42, projectId: "project-a", packId: 7 }))
      .toBe("/?workspace=pack&c=42&project=project-a&pack=7");
  });

  it("closes only the Pack workspace parameters and preserves the active conversation", () => {
    expect(closePackWorkspaceHref(new URLSearchParams("workspace=pack&c=42&project=project-a&pack=7")))
      .toBe("/?c=42");
  });

  it("hands a create command to the workbench exactly once", () => {
    stagePackCreateCommand("/pack create an executive brief");
    expect(takePackCreateCommand()).toBe("/pack create an executive brief");
    expect(takePackCreateCommand()).toBeNull();
  });
});
