import { describe, expect, it } from "vitest";
import {
  PERSONA_PROFILES,
  profileFor,
} from "@/components/onboarding/personaContent";
import type { Persona } from "@/types/onboarding";

const PERSONAS: Persona[] = ["developer", "project_owner", "admin", "owner"];

/**
 * Onboarding copy is data, so these are the invariants that keep it honest:
 * every persona is described, every one is told where its authority stops, and
 * more authority means more stated capability.
 */
describe("persona profiles", () => {
  it("describes every persona", () => {
    for (const persona of PERSONAS) {
      expect(PERSONA_PROFILES[persona]).toBeDefined();
    }
  });

  it("falls back to the member profile for an unknown persona", () => {
    expect(profileFor("invented" as Persona)).toBe(PERSONA_PROFILES.developer);
  });

  it("always states where authority stops", () => {
    // An unstated boundary becomes a support question later.
    for (const persona of PERSONAS) {
      const profile = profileFor(persona);
      expect(profile.boundary.length).toBeGreaterThan(40);
      expect(profile.mission.length).toBeGreaterThan(20);
      expect(profile.can.length).toBeGreaterThanOrEqual(3);
    }
  });

  it("gives more capabilities as authority increases", () => {
    const size = (persona: Persona) => profileFor(persona).can.length;
    expect(size("developer")).toBeLessThan(size("project_owner"));
    expect(size("project_owner")).toBeLessThanOrEqual(size("admin"));
    expect(size("admin")).toBeLessThanOrEqual(size("owner"));
  });

  it("points every persona at a concrete first action", () => {
    for (const persona of PERSONAS) {
      const { firstMove } = profileFor(persona);
      expect(firstMove.href.startsWith("/")).toBe(true);
      expect(firstMove.label.length).toBeGreaterThan(5);
      expect(firstMove.hint.length).toBeGreaterThan(20);
    }
  });

  it("tells elevated personas that they inherit member capabilities", () => {
    // Otherwise the admin screen reads as though asking questions is not part
    // of the job.
    for (const persona of ["project_owner", "admin", "owner"] as Persona[]) {
      const titles = profileFor(persona).can.map((c) => c.title.toLowerCase());
      expect(titles.some((title) => title.includes("everything"))).toBe(true);
    }
  });
});
