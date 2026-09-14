import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import KpiBar from "../components/KpiBar";
import { UNKNOWN_DISPLAY, loadableFromQuery, mapLoadable } from "../lib/loadable";
import type { Project } from "../types";

/**
 * "Not known yet" must never render as a confirmed value.
 *
 * The verified symptom: on a cold load the Projects page showed
 * "Projects 0 / Components 0 / Documents 0 / Last Activity Never" and then
 * replaced all four with the real figures. Both readings came from the same
 * empty array -- `reduce` over `[]` is 0 and `timeAgo(null)` is "Never" -- so
 * the component could not tell an unresolved query from an empty estate. It
 * reads as data loss.
 */

const project: Project = {
  id: "p1",
  display_name: "P1",
  repo_url: null,
  local_path: "/tmp/p1",
  platform: "talend",
  doc_generation_provider: null,
  source_type: "zip",
  status: "ready",
  last_scan_at: "2026-09-01T10:00:00Z",
  last_generate_at: null,
  last_rag_push_at: null,
  job_count: 429,
  doc_count: 12,
  error_message: null,
  last_ingested_commit_sha: null,
  auto_reingest_enabled: false,
  auto_reingest_last_checked_at: null,
  auto_reingest_error: null,
  git_update_summary: null,
  has_github_token: false,
  connection_kind: "pat",
  github_account_login: null,
  webhook_status: null,
  instruction_pack_dir: null,
  plugin_pack_id: null,
  created_at: null,
  updated_at: null,
};

describe("KpiBar unknown vs confirmed state", () => {
  it("renders no zeros and no 'Never' while the projects query is pending", () => {
    render(<KpiBar projects={{ status: "loading" }} />);

    expect(screen.queryByText("0")).not.toBeInTheDocument();
    expect(screen.queryByText("Never")).not.toBeInTheDocument();
    expect(screen.getAllByText(UNKNOWN_DISPLAY)).toHaveLength(4);
    // Every label is still present: the tiles keep their shape, only the
    // figures are withheld.
    for (const label of ["Projects", "Components", "Documents", "Last Activity"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("renders no zeros when the projects query failed", () => {
    render(<KpiBar projects={{ status: "error", error: new Error("500: boom") }} />);

    expect(screen.queryByText("0")).not.toBeInTheDocument();
    expect(screen.queryByText("Never")).not.toBeInTheDocument();
    expect(screen.getAllByText(UNKNOWN_DISPLAY)).toHaveLength(4);
  });

  it("renders 0 and 'Never' for a confirmed empty estate", () => {
    render(<KpiBar projects={{ status: "ready", value: [] }} />);

    // Three genuine zeros, and "Never" is now a statement the server backs.
    expect(screen.getAllByText("0")).toHaveLength(3);
    expect(screen.getByText("Never")).toBeInTheDocument();
    expect(screen.queryByText(UNKNOWN_DISPLAY)).not.toBeInTheDocument();
  });

  it("renders the real figures for a confirmed non-empty estate", () => {
    render(<KpiBar projects={{ status: "ready", value: [project] }} />);

    // Known figures animate up from 0, so the in-flight numbers are not
    // assertable -- but the placeholder must be gone the moment data arrives,
    // and "Never" must not appear for a project that was scanned.
    expect(screen.queryByText(UNKNOWN_DISPLAY)).not.toBeInTheDocument();
    expect(screen.queryByText("Never")).not.toBeInTheDocument();
    expect(screen.getByText(/ago$/)).toBeInTheDocument();
  });
});

describe("loadableFromQuery", () => {
  it("reports loading for a query with neither data nor error", () => {
    expect(loadableFromQuery({ data: undefined, isError: false })).toEqual({ status: "loading" });
  });

  it("reports error only when there is no data to show", () => {
    const error = new Error("500: boom");
    expect(loadableFromQuery({ data: undefined, isError: true, error })).toEqual({
      status: "error",
      error,
    });
  });

  it("prefers stale-but-real data over a placeholder during a failed refetch", () => {
    expect(
      loadableFromQuery({ data: [project], isError: true, error: new Error("500: boom") }),
    ).toEqual({ status: "ready", value: [project] });
  });

  it("distinguishes a confirmed empty response from loading", () => {
    expect(loadableFromQuery({ data: [], isError: false })).toEqual({
      status: "ready",
      value: [],
    });
  });

  it("passes loading and error through mapLoadable untouched", () => {
    expect(mapLoadable(loadableFromQuery({ data: undefined, isError: false }), () => 1)).toEqual({
      status: "loading",
    });
    expect(
      mapLoadable(loadableFromQuery({ data: { projects: [project] }, isError: false }), (d) => d.projects),
    ).toEqual({ status: "ready", value: [project] });
  });
});
