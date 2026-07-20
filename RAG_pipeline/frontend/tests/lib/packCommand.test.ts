import { describe, expect, it } from "vitest";
import { resolvePackCommand } from "@/lib/packCommand";

const projects = [
  { id: "project-a", display_name: "Project Alpha", platform: "talend" },
];

describe("resolvePackCommand", () => {
  it("keeps Pack creation in the conversational harness", () => {
    expect(resolvePackCommand("create", projects, "project-a", 41)).toEqual({
      href: "/?workspace=pack&c=41&project=project-a&pack=create",
      message: "/pack create",
    });
  });

  it("preserves a natural-language Pack brief", () => {
    expect(resolvePackCommand("create a supplier risk pack for procurement directors", projects)).toEqual({
      href: "/?workspace=pack&pack=create",
      message: "/pack create a supplier risk pack for procurement directors",
    });
  });

  it("resolves an accessible project and carries the requested Pack", () => {
    expect(resolvePackCommand('use "Executive Brief" for Project Alpha', projects, undefined, 41)).toEqual({
      href: "/?workspace=pack&c=41&project=project-a&pack=Executive+Brief",
    });
  });

  it("returns a visible error contract for an unknown project", () => {
    expect(resolvePackCommand("use Executive Brief for Missing Project", projects)).toEqual({
      error: "Project “Missing Project” is not available in your current scope.",
    });
  });
});
