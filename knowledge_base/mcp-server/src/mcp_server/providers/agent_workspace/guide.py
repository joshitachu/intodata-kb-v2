"""Render ``AGENT_GUIDE.md`` and ``GOAL.md``.

The guide is a **folder map**. It says what is in each directory and where to
look for what kind of question. It does not enumerate files, it does not
pre-decide what the integrations are, and it does not tell the agent how to
divide the reading.

All three properties are deliberate and each was bought with a failed run:

* **No file list.** The predecessor handed the agent a 38 MB fact graph as its
  primary interface -- 15,567 nodes, 39% of them ``state=unsupported``
  inventory rows -- on the theory that more index is more help. A catalogue of
  this workspace would be the same mistake in Markdown: every design file,
  every mapping table and a dozen scan artifacts, all of which the agent's own
  ``glob`` finds in one call and none of which it needs before it knows what
  it is looking for. The guide points at directories; the directories list
  themselves.
* **No analysis.** Deciding what the integrations are IS the work. A folder
  convention is worth one sentence of orientation; a Nexus-authored list of
  "the integrations in this estate" is Nexus doing the analysis badly and then
  anchoring the agent to it.
* **No reading regime.** This file used to instruct "Delegate: a worker per
  group or per integration, each reading its own units in full". That is a
  partitioning scheme dressed as advice: it fixes the unit of work before the
  question is known, and it is the mechanism this lane exists to remove.
  Coverage is an obligation on the OUTPUT (``instructions/investigation.md``);
  how to achieve it is the agent's decision.

The under-150-line ceiling is asserted by the test suite. It is not
aesthetic: a guide long enough to need its own table of contents has started
being a document about the workspace rather than a map of it.

This module is also the BOTTOM of the prose layer, and deliberately imports
nothing but ``contract``. Two things live here because more than one renderer
needs them and a second copy would be free to disagree:

* :func:`scope_mode_for_goal` -- whether this run is focused or whole-estate.
  ``instructions.py`` renders different obligations for the two, and the scope
  a file states has to match the scope ``GOAL.md`` states.
* :func:`ascii_only` -- the cp1252 fold. It used to exist twice, and the copy
  here was the one WITHOUT the typographic fold table, so an em dash in an
  operator's goal reached ``GOAL.md`` as ``?``.
"""

from __future__ import annotations

from typing import Literal

from mcp_server.providers.agent_workspace.contract import INSTRUCTION_FILES

#: Hard ceiling on ``AGENT_GUIDE.md``. See the module docstring.
MAX_GUIDE_LINES = 150

#: What the run was asked for, RENDERED rather than reconciled at read time.
#:
#: The two need opposite instructions. A whole-estate run must account for
#: every class of parsed evidence and every meaningful integration; a focused
#: run must investigate what the goal reaches, in depth, and is entitled to
#: leave the rest alone. Shipping both rules and letting the agent decide which
#: applies is what turned every focused request back into estate-wide work.
ScopeMode = Literal["whole_estate", "focused"]


def scope_mode_for_goal(goal: str | None, *, declared: ScopeMode | None = None) -> ScopeMode:
    """Which scope mode a run is in. One function, so the renderers agree.

    ``declared`` is the operator's choice and WINS. It exists because the goal
    string cannot carry this decision safely.

    **Inference is a fallback, and it fails toward the wider obligation.** The
    first spelling of this function returned ``whole_estate`` only for an empty
    goal or for :data:`DEFAULT_GOAL` exactly, and ``focused`` for everything
    else. Measured against the shipped configuration, that inverted the safe
    direction: ``AgentWorkspaceSettings.goal`` defaults to "Produce complete
    integration documentation for this estate", which is not ``DEFAULT_GOAL``,
    so EVERY default run classified as focused and silently dropped the
    estate-wide accountability rule from all four bootstrap files. A request
    for complete documentation would have been answered with a focused
    contract and nothing in the run would have said so.

    Narrowing is therefore never inferred from prose. An unstated mode is
    ``whole_estate``: asking for more accountability than a goal needs costs a
    little effort, while asking for less publishes an estate with parts
    silently out of scope, and only one of those is recoverable by reading the
    result. PRD 15.3 forbids prose-matching as a gate for the same reason it is
    wrong here -- a sentence is not a scope declaration.
    """

    if declared is not None:
        return declared
    return "whole_estate"


#: Typographic characters the upstream prose uses, mapped to ASCII. Generated
#: Markdown targets Windows cp1252, so it cannot ship as UTF-8; without the
#: fold, ``errors="replace"`` turns every em dash and every arrow in a flow
#: diagram into ``?``.
_ASCII_FOLD = {
    "—": "--",  # em dash
    "–": "-",  # en dash
    "→": "->",  # rightwards arrow
    "↓": "v",  # downwards arrow (flow diagrams)
    "─": "-",  # box drawing horizontal
    "├": "+",  # box drawing tee
    "└": "\\",  # box drawing corner
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    "…": "...",
    "•": "-",
}


def ascii_only(text: str) -> str:
    """Fold typographic characters, then force ASCII. cp1252 everywhere."""

    for source, replacement in _ASCII_FOLD.items():
        text = text.replace(source, replacement)
    return text.encode("ascii", errors="replace").decode("ascii")

_GUIDE = """\
# The workspace

You have the deterministic parse of a {platform} integration estate, the
instructions for reading it, and two directories you can write to.

Start with `INDEX.md`: it is the map of what this scan found and where each
class of evidence lives. Then `GOAL.md` for what is being asked, and
`instructions/house_rules.md` for the rules that are not negotiable. That is
the whole of the startup reading; everything else is opened when a question
needs it.

Everything outside `output/` and `notes/` is read-only evidence.

## The folders

**`instructions/`** -- how to do this work. {instruction_count} core files:
{instruction_lines}

For a documentation goal, open `instructions/investigation.md` and
`instructions/writing_the_docs.md` before substantive exploration. Open the
integration-model and topology guidance when you start recording those outputs.

`instructions/platform/` is on-demand reference, not startup reading: what the
scan's fields mean, how to work through a single design, and the verified
method for this platform, one file per guide. `instructions/platform/README.md`
says which one answers which question.

**`scan/`** -- the estate as the scanner parsed it, and the evidence of
record. `scan/jobs/<group>/<design>.json` is one file per design with its
components, connections, mappings, context and references. Beside it,
`contexts`, `connections`, `routines`, `xslt`, `services`, `structures`,
`tdm_maps`, `landscape`, `lineage`, `mapping_accounting`, `inventory` and
`capability_ledger` hold the estate-wide collections. No source tree is
staged: the scan is the evidence, and it is already credential-filtered.

Code bodies are not staged either -- a component's or routine's body is
replaced by a stated summary of it. `facts/parser_limitations.md` says so, and
`instructions/platform/reading_the_scan.md` says what to do about it. What is
staged in full is the mapping expressions, which are the field-level evidence.

**`facts/`** -- an INDEX to the evidence, computed deterministically with no
model in the loop. `units.json`, `dependencies.json`, `interfaces.json` and
`configuration.json` are rollups: they tell you what exists and which units
matter for a question. They do not tell you what anything DOES -- for that you
open the unit's own file under `scan/jobs/`.

That distinction is the one most easily missed here. `facts/units.json` lists
all of the designs and therefore reads like the whole estate; behind those rows
sit megabytes of per-design evidence that the rollups do not contain. Start
here to find your way; do not finish here.

`facts/navigation/` is the same material as one-line JSONL rows, split by
size. `grep` it to find the handful of designs a question is about instead of
loading a rollup whole. `facts/mappings/` is one exact, untruncated table per
mapper, which IS the evidence for field logic. `facts/parser_limitations.md`
names what the scanner could NOT read, and you should read it before
concluding anything is absent.

**`runtime/`** -- what production actually did, when a runtime mirror is
connected to this project. It states its own status either way; an empty
answer there means something specific, and the file says which.
`runtime/profile.md` is a compact orientation view; `runtime/evidence.json`
contains every safe structured unit record returned for this run and is the
source to inspect when a question needs the details hidden by the summary.

**`notes/`** -- yours, and the place the run is judged from.
`instructions/house_rules.md` states each of these in full:

- `notes/analysis/<group>.md` -- one analysis per group. This is where the
  investigation is recorded, and a finished run has one for every group.
- `notes/integrations/<id>.json` -- the integrations you establish, one per
  file. (`notes/integration_model.json`, the older single file, still loads.)
- `notes/coverage/` -- the roster of parsed designs, and the classification
  log you append to.
- `notes/completion.json` -- the completion claim, without which the run is
  unfinished work rather than finished documentation.
- `notes/progress.md` and anything else you want -- scratch.

These files survive a fresh session; keep them current enough that nothing
important has to be rediscovered.

**`output/`** -- the deliverable. `output/reference/` is already populated
with complete deterministic registers for mappings, interfaces, units,
dependencies, configuration, and known evidence limits. Preserve those files
and link your explanations to them; do not spend model context transcribing
them. Write the catalogue, README, and integration dossiers yourself.
Sub-directories are preserved. Topology is compiled from the same shared
integration model that drives the catalogue.

## How a {platform} estate is laid out

Designs are grouped into folders, and a group name may combine source and
target aliases. Treat that as orientation, not as a finding -- the folder is
what someone called it, and what the parsed design does is yours to establish.
Some groups are shared utilities that no convention describes at all.

A design is a job, a route, a joblet or a routelet. Jobs and routes run;
joblets and routelets are fragments other designs invoke. `scan/jobs/`
contains all four artifact kinds, and each file says which it is.

## Where to look for what

| Question | Index (which) | Evidence (what it does) |
|---|---|---|
| What exists, and how much | `INDEX.md`, `facts/units.json` | `scan/jobs/` |
| Which designs a question is about | `facts/navigation/` | `scan/jobs/` |
| What a design actually does | `facts/units.json` | **its own file under `scan/jobs/`** |
| What calls what | `facts/dependencies.json` | the caller's own file |
| What talks to the outside world | `facts/interfaces.json` | the component's params in its design file |
| How a field gets from A to B | `scan/lineage.json` | exact mapping facts; the complete client copy is already under `output/reference/` |
| What differs between environments | `facts/configuration.json` | `scan/contexts.json` |
| What custom code reaches for | `scan/routines.json` | the `code_withheld` summary on the component -- the body itself is not staged |
| What a stylesheet maps | -- | `scan/xslt.json` |
| Whether something failed in production | -- | `runtime/` |
| Whether the scanner saw it at all | `facts/parser_limitations.md` | `scan/inventory.json` |

The left column narrows the question. The right column answers it. A document
written only from the left column will be accurate and say nothing.

## Two things to get right

**Absence.** A file the scanner could not parse is not a file that does not
exist, a value shown as `[PASSWORD_REDACTED]` is not a value that is missing,
and a withheld code body is not a component without logic.
`facts/parser_limitations.md` tells you which is which. Claiming that
configuration is unavailable when it is merely redacted is the specific error
a client notices first.

**Names.** Designs are keyed on their scanner identity and labelled with their
display name; the two differ more often than you would expect. Never
fuzzy-match two names into one -- an estate that contains both `LoadCustomer`
and `LoadCustomers` contains two separately scheduled designs, and folding the
pair merges one job's evidence into another's.

## How much of this to read

The evidence is larger than one context. Nothing here partitions it for you,
assigns you units, or counts your sub-agents. Use the navigation views to find
connected evidence, keep the shared integration model current, and let the
goal determine how far the investigation expands.
`instructions/investigation.md` is where that is spelled out.
"""

_GOAL = """\
# The goal

{goal}

## What "done" means

Documentation in `output/` that a competent engineer who has never seen this
estate could use to answer a real question about it, and that a client can
read without meeting a single internal term.

`instructions/writing_the_docs.md` describes what the documentation must make
understandable. It is a completeness target, not a file checklist or work
order: you choose the route and the document boundaries.

`instructions/house_rules.md` is the part that is not negotiable.

## Scope

Scope mode: **{scope_mode}**

Project: `{project_id}`
Platform: `{platform}`
Designs available: {design_count} across {group_count} group(s).

{scope_semantics}

Those counts describe the evidence that is AVAILABLE to you. They are not a
quota and not a work order. There is no partitioning, no assigned unit list
and no per-document budget: work through what the goal needs, in whatever
order makes the result coherent.

## If you run out of room

Keep `notes/progress.md` current: what is finished in `output/`, what is
started, what is left, and anything you learned that a fresh session would
otherwise rediscover. If this session ends early, that file is what the next
one is given.
"""

#: Scope semantics when the run supplied no goal of its own. The estate is the
#: subject because nothing narrower was asked for.
_SCOPE_WHOLE_ESTATE = """\
No narrower request was made, so the whole estate is in scope: the result has
to account for everything material that was parsed, and for every meaningful
integration in it."""

#: Scope semantics when a goal WAS supplied. The previous version of this file
#: printed "The whole estate is in scope." unconditionally, directly underneath
#: whatever focused question the operator had asked -- two contradictory scope
#: statements on one page, with the machine-generated one reading as the
#: authoritative one.
_SCOPE_FROM_GOAL = """\
The goal above defines the scope. Start where it points and expand from there
only along paths that bear on it -- what it exchanges with, what transforms
it, what it depends on, what configures it, what runs it, and what breaks it.
When the evidence reveals a dependency the goal did not name but a reader
would need, follow it and say that you did. A part of the estate that bears on
none of this is out of scope, and saying so is an answer rather than a gap.

`INDEX.md` describes what exists and where. Under a focused goal it is a map,
not a work order: evidence that is unrelated to the goal does not have to be
mentioned to prove you considered it."""

#: Human label for each mode, used in the rendered files rather than the
#: internal identifier.
SCOPE_MODE_LABELS: dict[ScopeMode, str] = {
    "whole_estate": "whole estate",
    "focused": "focused",
}


def render_guide(*, platform: str) -> str:
    """The folder map. Deterministic, and under ``MAX_GUIDE_LINES``."""
    instruction_lines = "\n".join(
        f"  {index}. `instructions/{name}`" for index, name in enumerate(INSTRUCTION_FILES, 1)
    )
    text = _GUIDE.format(
        platform=(platform or "").strip() or "integration",
        instruction_count=len(INSTRUCTION_FILES),
        instruction_lines=instruction_lines,
    )
    return ascii_only(text)


def render_goal(
    *,
    goal: str,
    project_id: str,
    platform: str,
    design_count: int,
    group_count: int,
    scope_mode: ScopeMode | None = None,
) -> str:
    """The ask, and the scope semantics that follow from it.

    The scope paragraph is DERIVED from whether a goal was supplied, because a
    focused request and a whole-estate request need opposite instructions and
    the file has room for only one of them to be true. The mode is also NAMED,
    so the agent does not have to infer it from the tone of a paragraph -- and
    so ``instructions.build_instructions`` can be asked for the same mode.
    """
    resolved = (goal or "").strip() or DEFAULT_GOAL
    mode = scope_mode_for_goal(resolved, declared=scope_mode)
    text = _GOAL.format(
        goal=resolved,
        project_id=project_id,
        platform=(platform or "").strip() or "unknown",
        design_count=design_count,
        group_count=group_count,
        scope_mode=SCOPE_MODE_LABELS[mode],
        scope_semantics=(
            _SCOPE_WHOLE_ESTATE if mode == "whole_estate" else _SCOPE_FROM_GOAL
        ),
    )
    return ascii_only(text)


#: Used when a run supplies no goal. Deliberately a sentence about the estate
#: rather than a specification of the output -- the output is described by
#: ``instructions/writing_the_docs.md``, and repeating it here would give the
#: agent two sources for the same contract.
DEFAULT_GOAL = (
    "Document this integration estate for the organisation that runs it: what "
    "it exchanges with which systems, how each exchange works, what the rules "
    "and mappings are, where it is fragile, and what a reader would need to "
    "know before changing any of it."
)
