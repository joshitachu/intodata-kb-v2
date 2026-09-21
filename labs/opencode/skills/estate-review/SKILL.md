---
name: estate-review
description: Use when the user asks what a project is, what it does, how it is built, or asks to be oriented, briefed, onboarded onto, or brought up to speed on an integration estate they do not yet know. Triggers on "what is this project", "what does it do", "give me an overview", "walk me through it", "what am I looking at", "brief me on X". Do NOT use when the question is about one named job, component, table or field, and do NOT use when the question is about something failing right now.
---

# Estate review

A review answers "what is this and what should I worry about". It is a top-down
read, not a search. Work from the summary documents outward; never assemble an
overview out of whatever passages a keyword search happened to return.

## Procedure

1. **Confirm what you are reviewing.** `nexus_query_project_metadata` with
   `query_type=project_detail` for the bound project, or `project_list` if no
   project is bound. If nothing is bound, ask which project and stop.

2. **Read the top of the documentation, not the middle.** Search for the
   executive summary and the architecture map with
   `nexus_search_documentation`. Those two documents were written to answer
   exactly this question; everything else is detail underneath them. Two
   targeted searches beat one broad one.

3. **Get the real shape from the topology.** `nexus_read_topology_structure`
   with `operation=get_topology_overview`. This is the only source that is
   complete within its artifact: inventories, counts and the wiring between
   things. Documentation search cannot promise completeness and must never be
   used to produce a count.

4. **Reconcile before you write.** Where the two disagree, the topology owns
   what the code does and the documentation owns why it exists. If the project
   has no published topology, say the overview is documentation-only rather
   than presenting it as the full picture.

5. **Add operational reality only if it changes the verdict.** A single
   `nexus_read_runtime_evidence` call with `operation=list_runtime_health`
   turns "here is the design" into "here is the design, and this part of it is
   currently broken". Skip it if no runtime source is connected.

## What to report, in this order

1. **What the estate is** -- one paragraph, then the business domains it
   covers. Name the domains in the client's own vocabulary, not in scanner
   terms.
2. **Ranked risks** -- highest first, each with why it matters to this client
   rather than in the abstract. Concentration on one interface, undocumented
   transformations, a dead or unmonitored chain, a single point of failure.
   Three to five is a review; ten is a list.
3. **Next actions** -- what the reader could usefully do next, in their
   language. Tie each one to a risk above.

## Rules

- Do not list every component. A review that enumerates is a review nobody
  finishes reading.
- Every count comes from the topology or from project metadata. Never repeat a
  number you read in a documentation passage as if it were an inventory.
- If the project has no generated documentation, say so plainly and offer what
  the topology alone can support. Do not pad the gap with generic integration
  advice.
