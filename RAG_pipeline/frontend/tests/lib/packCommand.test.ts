import { describe, expect, it } from "vitest";
import { resolvePackCommand } from "@/lib/packCommand";

const projects = [
  { id: "project-a", display_name: "Project Alpha", platform: "talend" },
];

describe("resolvePackCommand", () => {
  it("keeps Pack creation in the conversational harness", () => {
    expect(resolvePackCommand("create", projects, "project-a")).toEqual({
      message: "/pack create",
    });
  });

  it("preserves a natural-language Pack brief", () => {
    expect(resolvePackCommand("create a supplier risk pack for procurement directors", projects)).toEqual({
      message: "/pack create a supplier risk pack for procurement directors",
    });
  });

  it("resolves an accessible project and carries the requested Pack", () => {
    expect(resolvePackCommand('use "Executive Brief" for Project Alpha', projects)).toEqual({
      href: "/pack?project=project-a&pack=Executive+Brief&mode=stack",
    });
  });

  it("returns a visible error contract for an unknown project", () => {
    expect(resolvePackCommand("use Executive Brief for Missing Project", projects)).toEqual({
      error: "Project “Missing Project” is not available in your current scope.",
    });
  });
});
