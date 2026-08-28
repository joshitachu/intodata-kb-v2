import { describe, expect, it } from "vitest";
import { ALL_TOURS, tourFor } from "@/components/tour/tours";
import { applicableSteps } from "@/components/tour/types";
import type { Persona } from "@/types/onboarding";

/**
 * Structural rules the walkthrough content must keep satisfying.
 *
 * The old tour was one hardcoded list shared by every role. These assertions
 * are what stop it drifting back to that: ids stay unique and stable, every
 * persona is covered, authority increases the amount taught, and an admin step
 * can never be shown to someone without the permission behind it.
 */

const PERSONAS: Persona[] = ["developer", "project_owner", "admin", "owner"];

const ALL_PERMISSIONS = [
  "manage_users",
  "manage_roles",
  "assign_projects",
  "view_all_projects",
  "manage_projects",
  "view_assigned_projects",
  "query_rag",
  "ingest_documents",
  "view_audit_logs",
  "create_projects",
];

describe("walkthrough definitions", () => {
  it("defines a tour for every persona", () => {
    for (const persona of PERSONAS) {
      expect(tourFor(persona).variant).toBe(persona);
    }
  });

  it("falls back to the member tour for an unrecognized persona", () => {
    // A frontend deployed ahead of a backend that adds a persona must still
    // render something rather than crashing on an undefined definition.
    expect(tourFor("something-new" as Persona).variant).toBe("developer");
  });

  it("uses unique step ids within each tour", () => {
    // Ids are the persisted resume key; a duplicate would make progress
    // ambiguous and could resume on the wrong step.
    for (const persona of PERSONAS) {
      const ids = tourFor(persona).steps.map((step) => step.id);
      expect(new Set(ids).size).toBe(ids.length);
    }
  });

  it("keeps one id per step across all tours", () => {
    // The same step reused in several personas must carry the same id, so
    // finishing it once is recorded once.
    const byId = new Map<string, string>();
    for (const definition of Object.values(ALL_TOURS)) {
      for (const step of definition.steps) {
        const existing = byId.get(step.id);
        if (existing) expect(step.title).toBe(existing);
        byId.set(step.id, step.title);
      }
    }
    expect(byId.size).toBeGreaterThan(6);
  });

  it("teaches more as authority increases", () => {
    const count = (persona: Persona) =>
      applicableSteps(tourFor(persona).steps, {
        hasPermission: () => true,
      }).length;

    expect(count("developer")).toBeLessThan(count("project_owner"));
    expect(count("project_owner")).toBeLessThan(count("admin"));
    expect(count("admin")).toBeLessThan(count("owner"));
  });

  it("starts every tour with the core value loop", () => {
    // Someone who stops after the first chapter should still be able to use
    // the product, so asking a question comes before administration.
    for (const persona of PERSONAS) {
      expect(tourFor(persona).steps[0].id).toBe("chat.input");
    }
  });

  it("ends every tour on a step that needs no anchor", () => {
    // The closing step must be showable even when nothing else resolved.
    for (const persona of PERSONAS) {
      const steps = tourFor(persona).steps;
      expect(steps[steps.length - 1].target).toBeUndefined();
    }
  });

  it("gates every administrative step behind a permission", () => {
    const memberSteps = applicableSteps(tourFor("owner").steps, {
      hasPermission: (codename) => codename === "query_rag",
    });
    const ids = memberSteps.map((step) => step.id);

    // A member holding only query_rag must never be walked through admin
    // surfaces, even when running the Owner tour definition.
    expect(ids.some((id) => id.startsWith("admin."))).toBe(false);
    expect(ids.some((id) => id.startsWith("owner."))).toBe(false);
  });

  it("keeps every step's copy short enough to be read", () => {
    for (const definition of Object.values(ALL_TOURS)) {
      for (const step of definition.steps) {
        expect(step.title.length).toBeLessThanOrEqual(60);
        expect(step.body.length).toBeLessThanOrEqual(260);
        expect(step.chapter.length).toBeGreaterThan(0);
      }
    }
  });

  it("gives each step a route or no target at all", () => {
    // A targeted step with no route would only resolve by luck, depending on
    // whichever page the user happened to be on.
    for (const definition of Object.values(ALL_TOURS)) {
      for (const step of definition.steps) {
        if (step.target) expect(step.route).toBeTruthy();
      }
    }
  });

  it("requests only permissions the RBAC system defines", () => {
    // A typo'd codename silently resolves to "denied", hiding the step from
    // everyone with no error anywhere.
    for (const definition of Object.values(ALL_TOURS)) {
      for (const step of definition.steps) {
        for (const codename of step.guard?.permissions ?? []) {
          expect(ALL_PERMISSIONS).toContain(codename);
        }
      }
    }
  });
});
