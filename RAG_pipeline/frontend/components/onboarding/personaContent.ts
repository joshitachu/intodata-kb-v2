import type { Persona } from "@/types/onboarding";

/**
 * What each persona is told about their own role during first-run onboarding.
 *
 * Kept as data rather than JSX branches so the welcome wizard, the "what you
 * can do" step and the product guide can render the same statements without
 * three copies drifting apart — the old flow described roles in prose inside
 * `/guide` only, where a member being onboarded never saw it.
 */

export interface PersonaCapability {
  title: string;
  detail: string;
}

export interface PersonaProfile {
  /** Shown as a role chip. */
  label: string;
  /** One sentence: what this person is here to do. */
  mission: string;
  /** What they can do. Phrased as capabilities, not permission codenames. */
  can: PersonaCapability[];
  /**
   * Where their authority stops. Stated plainly on purpose: unclear boundaries
   * generate support requests ("why can't I see project X?") that a single
   * sentence at onboarding time prevents.
   */
  boundary: string;
  /** First thing worth doing, and where. */
  firstMove: { label: string; href: string; hint: string };
}

export const PERSONA_PROFILES: Record<Persona, PersonaProfile> = {
  developer: {
    label: "Member",
    mission:
      "Find answers about the integration landscape without waiting on whoever built it.",
    can: [
      {
        title: "Ask questions in plain language",
        detail:
          "Query the documentation and code evidence for the projects you have been given access to.",
      },
      {
        title: "Follow every answer to its source",
        detail:
          "Each answer cites the document and section it came from, so you can verify it rather than trust it.",
      },
      {
        title: "Explore the topology",
        detail:
          "Move from a system down to the job and component that implements it, and back up again.",
      },
    ],
    boundary:
      "You see the projects you are assigned to. Ask an admin if something you expect is missing — visibility is granted per project, not per person.",
    firstMove: {
      label: "Ask your first question",
      href: "/",
      hint: "Start broad. \"What does this project do?\" is a perfectly good first query.",
    },
  },

  project_owner: {
    label: "Project owner",
    mission:
      "Operate the projects you own: keep their evidence current and their members correct.",
    can: [
      {
        title: "Everything a member can",
        detail: "Ask questions, follow citations, and navigate the topology.",
      },
      {
        title: "Connect and scan sources",
        detail:
          "Point a project at a repository or upload an archive, then scan it to extract technical facts.",
      },
      {
        title: "Generate and publish documentation",
        detail:
          "Turn scan results into project docs, review them, and publish them to the knowledge base.",
      },
      {
        title: "Manage who is on your project",
        detail: "Assign and remove members for the projects you own.",
      },
    ],
    boundary:
      "Project ownership is scoped to your projects. It does not include organization-wide member administration, roles, or security settings.",
    firstMove: {
      label: "Review your project sources",
      href: "/settings/sources",
      hint: "Check that each project has a connected source and a recent scan.",
    },
  },

  admin: {
    label: "Admin",
    mission:
      "Run day-to-day access and keep the knowledge base fed with current evidence.",
    can: [
      {
        title: "Everything a member can",
        detail: "Ask questions, follow citations, and navigate the topology.",
      },
      {
        title: "Approve access and set roles",
        detail:
          "Decide who gets in and what authority they arrive with — including whether they are an admin.",
      },
      {
        title: "Create and operate projects",
        detail:
          "Create projects, connect sources, run scans, and publish generated documentation.",
      },
      {
        title: "Inspect operational evidence",
        detail: "Review scans, usage, and the audit trail for what changed and when.",
      },
    ],
    boundary:
      "You cannot modify the Owner account or change role definitions and deployment-wide security settings. Those stay with the Owner.",
    firstMove: {
      label: "Check pending access requests",
      href: "/admin/organization?tab=access",
      hint: "Approving a request now asks for a role — that choice is what grants admin.",
    },
  },

  owner: {
    label: "Owner",
    mission:
      "Hold deployment-wide authority: roles, security, and the exceptions nobody else can resolve.",
    can: [
      {
        title: "Everything an admin can",
        detail: "Access, projects, sources, documentation, and operational evidence.",
      },
      {
        title: "Define roles and permissions",
        detail:
          "Create roles and decide which permissions each one carries, for the whole deployment.",
      },
      {
        title: "Handle recovery and exceptions",
        detail:
          "Reset credentials, verify mail delivery, and resolve access problems no admin can.",
      },
      {
        title: "See deployment cost and audit",
        detail:
          "Token spend, query volume, and the complete audit trail across every user and project.",
      },
    ],
    boundary:
      "Owner access is protected and should not be shared. Delegate routine member and project work to Admins and keep this account for authority and exceptions.",
    firstMove: {
      label: "Define least-privilege roles",
      href: "/admin/organization?tab=roles",
      hint: "Set roles up before inviting people, so approvals have the right option to pick.",
    },
  },
};

export function profileFor(persona: Persona): PersonaProfile {
  return PERSONA_PROFILES[persona] ?? PERSONA_PROFILES.developer;
}
