import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  AttachedProjectsCell,
  SourceAttachmentsDialog,
  eligibleProjects,
  extraExposedProjects,
} from "@/app/admin/runtime/SourceSharingPanel";
import type { RuntimeSourceReadGrant } from "@/types/runtime";

/**
 * A runtime source is a vendor tenant with NO owning project: projects attach
 * to it and detach from it, at registration or long afterwards.
 *
 * Two failures are guarded here. The first is an attachment that looks
 * narrower than it is -- `exposes_project_ids` is the only signal that it also
 * hands over a third project's rows, and a field left undeclared anywhere on
 * the path is silently `undefined` in the browser. The second is a source with
 * no attachments reading as broken: that is a normal intermediate state, and
 * presenting it as a fault sends operators hunting for a problem that is not
 * there.
 */

const PROJECTS = [
  { id: "first-proj", display_name: "First project" },
  { id: "reader-proj", display_name: "Reader project" },
  { id: "third-proj", display_name: "Third project" },
];

/** No project on it: a source is identified by its id and label, nothing else. */
const SOURCE = { id: 7, label: "Talend Cloud EU" };

function grant(overrides: Partial<RuntimeSourceReadGrant> = {}): RuntimeSourceReadGrant {
  return {
    id: 1,
    source_id: 7,
    project_id: "reader-proj",
    // The source's internal row stamp, not a project. Renamed from
    // `source_project_id` when sources stopped having an owner.
    source_estate_key: "estate-7",
    source_label: "Talend Cloud EU",
    environment_ids: null,
    window_days: null,
    exposes_project_ids: ["estate-7"],
    created_at: "2026-09-01T10:00:00Z",
    created_by: "3",
    ...overrides,
  };
}

type StubResult = { status?: number; body?: unknown };
type RouteHandler = (init?: RequestInit) => StubResult;

/**
 * Route the component's fetches by URL and method.
 *
 * Method-aware on purpose: attaching a project is a POST followed by a GET that
 * re-reads the list, and a stub that answered both with the POST payload would
 * make the refetch fail while the assertion still passed.
 */
function stubFetch(routes: Record<string, RouteHandler>) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const fetchMock = vi.fn(async (input: unknown, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, init });
    const method = (init?.method ?? "GET").toUpperCase();
    const key =
      Object.keys(routes).find((route) => route === `${method} ${url}`) ??
      Object.keys(routes).find(
        (route) => route.startsWith(`${method} `) && url.startsWith(route.slice(method.length + 1)),
      ) ??
      Object.keys(routes).find((route) => url.startsWith(route));
    const result = key ? routes[key](init) : { status: 404, body: { detail: "no stub" } };
    const status = result.status ?? 200;
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => result.body ?? {},
    } as unknown as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("eligibleProjects", () => {
  it("drops every already-attached project", () => {
    expect(
      eligibleProjects(PROJECTS, [
        grant({ id: 1, project_id: "first-proj" }),
        grant({ id: 2, project_id: "reader-proj" }),
      ]).map((project) => project.id),
    ).toEqual(["third-proj"]);
  });

  /**
   * The old model excluded the source's "owning" project on the grounds that it
   * already read its own source. There is no owner now, so nothing is excluded
   * except the projects actually attached.
   */
  it("offers every visible project when nothing is attached yet", () => {
    expect(eligibleProjects(PROJECTS, []).map((project) => project.id)).toEqual([
      "first-proj",
      "reader-proj",
      "third-proj",
    ]);
  });

  it("returns nothing when every visible project is attached", () => {
    expect(
      eligibleProjects(
        PROJECTS,
        PROJECTS.map((project, index) => grant({ id: index + 1, project_id: project.id })),
      ),
    ).toEqual([]);
  });
});

describe("extraExposedProjects", () => {
  it("is empty when the attachment only reaches the source's own estate", () => {
    expect(extraExposedProjects(grant({ exposes_project_ids: ["estate-7"] }))).toEqual([]);
  });

  it("names the projects beyond the source's own estate", () => {
    expect(
      extraExposedProjects(grant({ exposes_project_ids: ["estate-7", "third-proj"] })),
    ).toEqual(["third-proj"]);
  });
});

describe("AttachedProjectsCell — what the row says before anything is clicked", () => {
  it("makes attaching the obvious next action when nothing is attached", () => {
    render(
      <AttachedProjectsCell
        grants={[]}
        loading={false}
        error={null}
        canManage
        onOpen={() => undefined}
      />,
    );
    expect(screen.getByRole("button", { name: "Attach a project" })).toBeInTheDocument();
  });

  /**
   * A tenant registered before any project is a normal intermediate state. It
   * must not be dressed as a fault, or the operator goes looking for a failure
   * that never happened.
   */
  it("does not present an unattached source as an error", () => {
    render(
      <AttachedProjectsCell
        grants={[]}
        loading={false}
        error={null}
        canManage
        onOpen={() => undefined}
      />,
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button")).toHaveAttribute(
      "title",
      expect.stringContaining("mirrored once and polled once"),
    );
  });

  it("states how many projects are attached", () => {
    render(
      <AttachedProjectsCell
        grants={[grant({ id: 1, project_id: "reader-proj" }), grant({ id: 2, project_id: "third-proj" })]}
        loading={false}
        error={null}
        canManage
        onOpen={() => undefined}
      />,
    );
    expect(screen.getByRole("button", { name: /2 projects attached/ })).toBeInTheDocument();
  });

  it("uses the singular for one attachment", () => {
    render(
      <AttachedProjectsCell
        grants={[grant()]}
        loading={false}
        error={null}
        canManage
        onOpen={() => undefined}
      />,
    );
    expect(screen.getByRole("button", { name: /^1 project attached$/ })).toBeInTheDocument();
  });

  it("shows plain text, not an action, for a reader-only user with nothing to open", () => {
    render(
      <AttachedProjectsCell
        grants={[]}
        loading={false}
        error={null}
        canManage={false}
        onOpen={() => undefined}
      />,
    );
    expect(screen.getByText("No projects attached")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("never renders an unknown attachment state as 'no projects attached'", () => {
    const { rerender } = render(
      <AttachedProjectsCell
        grants={undefined}
        loading
        error={null}
        canManage
        onOpen={() => undefined}
      />,
    );
    expect(screen.getByText("Checking...")).toBeInTheDocument();

    rerender(
      <AttachedProjectsCell
        grants={undefined}
        loading={false}
        error="Runtime service unavailable"
        canManage
        onOpen={() => undefined}
      />,
    );
    expect(screen.getByText("Attachments unavailable")).toBeInTheDocument();
    expect(screen.queryByText("No projects attached")).not.toBeInTheDocument();
  });
});

describe("SourceAttachmentsDialog", () => {
  function open(props: Partial<Parameters<typeof SourceAttachmentsDialog>[0]> = {}) {
    return render(
      <SourceAttachmentsDialog
        source={SOURCE}
        projects={PROJECTS}
        canManage
        onClose={() => undefined}
        onChanged={() => undefined}
        {...props}
      />,
    );
  }

  it("says the tenant is mirrored once, so an attachment adds no vendor load", async () => {
    stubFetch({ "/api/runtime/sources/7/readers": () => ({ body: { grants: [], total: 0 } }) });
    open();
    expect(await screen.findByText(/mirrored once and polled once/i)).toBeInTheDocument();
  });

  /**
   * The ownerless source at rest. It is registered and mirrored; it simply has
   * no readers, and attaching one is offered rather than demanded.
   */
  it("names the unattached state plainly and still offers the attach form", async () => {
    stubFetch({ "/api/runtime/sources/7/readers": () => ({ body: { grants: [], total: 0 } }) });
    open();

    expect(await screen.findByText(/No projects are attached yet/)).toBeInTheDocument();
    expect(screen.getByText(/registered and mirrored/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Attach this project/ })).toBeEnabled();
  });

  it("renders the server's own message when the attachment list fails", async () => {
    stubFetch({
      "/api/runtime/sources/7/readers": () => ({
        status: 503,
        body: { detail: "Runtime store is offline" },
      }),
    });
    open();
    expect(await screen.findByRole("alert")).toHaveTextContent("Runtime store is offline");
  });

  it("distinguishes 'all environments' from an attachment that can read nothing", async () => {
    stubFetch({
      "/api/runtime/sources/7/readers": () => ({
        body: {
          grants: [
            grant({ id: 1, project_id: "reader-proj", environment_ids: null }),
            grant({ id: 2, project_id: "third-proj", environment_ids: [] }),
          ],
          total: 2,
        },
      }),
    });
    open();
    expect(await screen.findByText("All environments")).toBeInTheDocument();
    expect(
      screen.getByText("No environments - this project can read nothing"),
    ).toBeInTheDocument();
  });

  it("warns on an existing attachment that also exposes a third project", async () => {
    stubFetch({
      "/api/runtime/sources/7/readers": () => ({
        body: {
          grants: [grant({ exposes_project_ids: ["estate-7", "third-proj"] })],
          total: 1,
        },
      }),
    });
    open();
    expect(
      await screen.findByText(/Also exposes data belonging to Third project/),
    ).toBeInTheDocument();
  });

  it("shares everything by default: no environment narrowing and no window", async () => {
    let attached = false;
    const calls = stubFetch({
      "GET /api/runtime/sources/7/readers": () => ({
        body: attached
          ? { grants: [grant({ project_id: "first-proj" })], total: 1 }
          : { grants: [], total: 0 },
      }),
      "POST /api/runtime/sources/7/readers": () => {
        attached = true;
        return { body: { grant: grant({ project_id: "first-proj" }) } };
      },
    });
    open();
    fireEvent.click(await screen.findByRole("button", { name: /Attach this project/ }));

    await waitFor(() => expect(calls.some((call) => call.init?.method === "POST")).toBe(true));
    const body = JSON.parse(
      String(calls.find((call) => call.init?.method === "POST")?.init?.body),
    );
    // `environment_ids: null` is "every mirrored environment"; `[]` would be a
    // deliberate "nothing". The default path must send null, never [].
    expect(body).toEqual({
      project_id: "first-proj",
      environment_ids: null,
      window_days: null,
    });
    // The list is re-read after the write, so the row count cannot go stale.
    expect(await screen.findByText("First project")).toBeInTheDocument();
  });

  /**
   * The regression this whole feature turns on. If `exposes_project_ids` is
   * dropped anywhere between the backend and this component, the operator is
   * told nothing and a third project's runtime data leaves with the attachment.
   */
  it("names the other projects a new attachment also exposes", async () => {
    const exposing = grant({
      project_id: "reader-proj",
      exposes_project_ids: ["estate-7", "third-proj"],
    });
    let attached = false;
    stubFetch({
      "GET /api/runtime/sources/7/readers": () => ({
        body: attached ? { grants: [exposing], total: 1 } : { grants: [], total: 0 },
      }),
      "POST /api/runtime/sources/7/readers": () => {
        attached = true;
        return { body: { grant: exposing } };
      },
    });
    open({ projects: [PROJECTS[1], PROJECTS[2]] });
    fireEvent.click(await screen.findByRole("button", { name: /Attach this project/ }));

    const notice = await screen.findByRole("status");
    expect(notice).toHaveTextContent("Reader project");
    expect(notice).toHaveTextContent("Third project");
  });

  it("stays silent when the attachment reaches only the source's own estate", async () => {
    const narrow = grant({ project_id: "reader-proj", exposes_project_ids: ["estate-7"] });
    let attached = false;
    stubFetch({
      "GET /api/runtime/sources/7/readers": () => ({
        body: attached ? { grants: [narrow], total: 1 } : { grants: [], total: 0 },
      }),
      "POST /api/runtime/sources/7/readers": () => {
        attached = true;
        return { body: { grant: narrow } };
      },
    });
    open();
    fireEvent.click(await screen.findByRole("button", { name: /Attach this project/ }));

    // Wait for the write to land, then assert nothing was claimed about it.
    expect(await screen.findByText("Reader project")).toBeInTheDocument();
    expect(screen.queryByText(/can now also read runtime data/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Also exposes data belonging to/)).not.toBeInTheDocument();
  });

  it("sends the chosen environments and window when the optional narrowing is used", async () => {
    const calls = stubFetch({
      "GET /api/runtime/sources/7/readers": () => ({
        body: { grants: [grant({ id: 9, project_id: "first-proj" })], total: 1 },
      }),
      "POST /api/runtime/sources/7/readers": () => ({
        body: { grant: grant({ environment_ids: ["env-prod"], window_days: 30 }) },
      }),
      "GET /api/runtime/doc-read-scope": () => ({
        body: {
          available_environments: [
            { environment_id: "env-prod", name: "PROD", run_count: 900 },
            { environment_id: "env-uat", name: "UAT", run_count: 12 },
          ],
        },
      }),
    });
    open();

    fireEvent.click(await screen.findByText("Limit what they can read (optional)"));
    const prod = await screen.findByRole("checkbox", { name: /PROD/ });

    // With the section open and nothing picked, the attachment is blocked rather
    // than quietly sent as [] -- which would read no data at all.
    expect(screen.getByRole("button", { name: /Attach this project/ })).toBeDisabled();
    expect(screen.getByText(/An empty selection would share nothing/)).toBeInTheDocument();

    fireEvent.click(prod);
    fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: /Attach this project/ }));

    await waitFor(() => expect(calls.some((call) => call.init?.method === "POST")).toBe(true));
    expect(
      JSON.parse(String(calls.find((call) => call.init?.method === "POST")?.init?.body)),
    ).toEqual({
      project_id: "reader-proj",
      environment_ids: ["env-prod"],
      window_days: 30,
    });
  });

  /**
   * The environment list is read through an ATTACHED project, because
   * `/doc-read-scope` answers for a project the caller can see. With nothing
   * attached there is no such project, and the section says so rather than
   * showing an empty list that would read as "the mirror is empty".
   */
  it("explains why there are no environments to narrow before the first attachment", async () => {
    const calls = stubFetch({
      "GET /api/runtime/sources/7/readers": () => ({ body: { grants: [], total: 0 } }),
    });
    open();

    fireEvent.click(await screen.findByText("Limit what they can read (optional)"));

    expect(await screen.findByText(/No project is attached yet/)).toBeInTheDocument();
    // Never queried with the estate key: it is not a project and would 404.
    expect(calls.some((call) => call.url.includes("doc-read-scope"))).toBe(false);
  });

  it("refuses a history window outside the range the backend accepts", async () => {
    stubFetch({
      "GET /api/runtime/sources/7/readers": () => ({
        body: { grants: [grant({ id: 9, project_id: "first-proj" })], total: 1 },
      }),
      "GET /api/runtime/doc-read-scope": () => ({
        body: { available_environments: [{ environment_id: "env-prod", name: "PROD", run_count: 9 }] },
      }),
    });
    open();

    fireEvent.click(await screen.findByText("Limit what they can read (optional)"));
    fireEvent.click(await screen.findByRole("checkbox", { name: /PROD/ }));
    fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "900" } });

    expect(screen.getByRole("button", { name: /Attach this project/ })).toBeDisabled();
    expect(screen.getByText(/between 1 and 365 days/)).toBeInTheDocument();
  });

  it("reports the backend's refusal verbatim when an attachment is rejected", async () => {
    stubFetch({
      "GET /api/runtime/sources/7/readers": () => ({ body: { grants: [], total: 0 } }),
      "POST /api/runtime/sources/7/readers": () => ({
        status: 409,
        body: { detail: "Project reader-proj is already attached to this source" },
      }),
    });
    open();
    fireEvent.click(await screen.findByRole("button", { name: /Attach this project/ }));

    expect(
      await screen.findByText("Project reader-proj is already attached to this source"),
    ).toBeInTheDocument();
  });

  it("says so rather than showing an empty dropdown when nothing is eligible", async () => {
    stubFetch({
      "/api/runtime/sources/7/readers": () => ({
        body: {
          grants: PROJECTS.map((project, index) =>
            grant({ id: index + 1, project_id: project.id }),
          ),
          total: 3,
        },
      }),
    });
    open();
    expect(
      await screen.findByText(/Every project you can see is already attached/),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Attach this project/ }),
    ).not.toBeInTheDocument();
  });

  it("confirms before detaching a project", async () => {
    let detached = false;
    const calls = stubFetch({
      "GET /api/runtime/sources/7/readers": () => ({
        body: detached ? { grants: [], total: 0 } : { grants: [grant()], total: 1 },
      }),
      "DELETE /api/runtime/sources/7/readers/reader-proj": () => {
        detached = true;
        return { status: 204 };
      },
    });
    open();

    fireEvent.click(await screen.findByRole("button", { name: "Detach Reader project" }));
    expect(calls.some((call) => call.init?.method === "DELETE")).toBe(false);
    expect(screen.getByRole("alertdialog")).toHaveTextContent(/Detach Reader project\?/);

    fireEvent.click(screen.getByRole("button", { name: /^Detach$/ }));
    await waitFor(() =>
      expect(
        calls.find((call) => call.init?.method === "DELETE")?.url,
      ).toBe("/api/runtime/sources/7/readers/reader-proj"),
    );
    expect(await screen.findByText(/No projects are attached yet/)).toBeInTheDocument();
  });

  it("keeps the project attached when the confirmation is cancelled", async () => {
    const calls = stubFetch({
      "GET /api/runtime/sources/7/readers": () => ({
        body: { grants: [grant()], total: 1 },
      }),
    });
    open();

    fireEvent.click(await screen.findByRole("button", { name: "Detach Reader project" }));
    fireEvent.click(screen.getByRole("button", { name: "Keep attached" }));

    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(calls.some((call) => call.init?.method === "DELETE")).toBe(false);
  });

  it("is visible but not actionable without runtime-source write access", async () => {
    stubFetch({
      "/api/runtime/sources/7/readers": () => ({
        body: { grants: [grant()], total: 1 },
      }),
    });
    open({ canManage: false });

    expect(await screen.findByText("Reader project")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Attach this project/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Detach/ })).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      /changing that needs runtime source write access/i,
    );
  });
});
