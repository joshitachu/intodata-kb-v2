"""The seam every part of the agent-workspace lane agrees on.

Two things live here and nothing else: the on-disk workspace layout, and the
provider-neutral run contract. They are in one module on purpose. The workspace
builder writes the layout, the provider adapters mount it, and the publisher
reads it back; if any of the three carried its own copy of these names they
would drift, and the drift would only show up as an agent reading an empty
directory.

Design rules this module encodes, from ``.plans/plan.md``:

* **The agent is handed a workspace and a goal. Nothing else.** There is no
  fact graph, no scope seed, no output schema, no expected-outcome string and
  no ``provider_options`` escape hatch. The predecessor's
  ``InvestigationRequest`` carried all five; three of them were never read by
  the transport, and the two that were carried the domain's JSON contract into
  what is supposed to be mechanical execution.
* **Ceilings are whole-run circuit breakers.** ``ResourceCeiling`` is reused
  verbatim from the coding-agent models because it is already correct: wall
  time, processed tokens, cost. Nothing here may express a per-job, per-group,
  per-document or per-session quota.
* **Restart, never resume-or-fail.** ``AgentRunResult`` has no required session
  id. A provider that loses its session reports ``failed`` with a reason and
  the runner starts a fresh one over the same workspace, the same goal, the
  existing ``output/`` and a short checkpoint summary. A lost session is a
  provider inconvenience, not a correctness event.
* **Only two directories are writable.** ``output/`` is the deliverable and
  ``notes/`` is the agent's own scratch. Everything else is evidence and is
  read-only, which is what makes the publish-time path check meaningful.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mcp_server.providers.coding_agent.models import ResourceCeiling, UsageReceipt

CONTRACT = "agent-workspace-v1"

# --------------------------------------------------------------------------
# Workspace layout
#
# Every path is relative to the staging directory the run was given. Nothing
# starts with an underscore: the agent reads these names, and an underscore is
# this codebase's marker for "internal, never shown to a client", which is a
# distinction the agent has no way to make and should not have to.
# --------------------------------------------------------------------------

AGENT_GUIDE = Path("AGENT_GUIDE.md")
GOAL = Path("GOAL.md")
INDEX = Path("INDEX.md")

INSTRUCTIONS = Path("instructions")
SCAN = Path("scan")
FACTS = Path("facts")
#: The searchable index layer. NOT :data:`INDEX`, which is ``INDEX.md``, the
#: one-page map. This is the directory of JSONL views it points into, and the
#: two are a rename away from silently becoming one name.
INDEX_DIR = Path("index")
RUNTIME = Path("runtime")
NOTES = Path("notes")
OUTPUT = Path("output")

#: Read-only to the agent. These are evidence and method.
#:
#: ``index/`` belongs here for the same reason ``facts/`` does: it is a
#: deterministic projection of the approved scan, so a modification to it is
#: evidence tampering and must fail the integrity digest. Leaving it out would
#: put the run's primary navigation surface outside both the digest and the
#: publication cleanup -- silently editable, and then shipped.
EVIDENCE_ROOTS: tuple[Path, ...] = (INSTRUCTIONS, SCAN, FACTS, INDEX_DIR, RUNTIME)

#: Loose read-only files at the workspace root. Separate from
#: ``EVIDENCE_ROOTS`` because they are files rather than directories, and every
#: consumer that walks one needs the other: the integrity digest, the
#: publication cleanup and the "what may the agent not modify" tests.
EVIDENCE_FILES: tuple[Path, ...] = (AGENT_GUIDE, GOAL, INDEX)

#: The only writable roots. Publish rejects anything outside ``OUTPUT``.
WRITABLE_ROOTS: tuple[Path, ...] = (OUTPUT, NOTES)

#: The five core instruction files, in the order the guide introduces them.
#:
#: ``house_rules`` is FIRST because it is the only one of the five that is read
#: at startup. The predecessor's set was five files totalling ~120 KB, two of
#: which (``reading_the_scan``, ``talend_semantics``) were a field dictionary
#: and a 2,063-line concatenated platform method -- both of them reference
#: material that an investigation opens when it needs them, and neither of them
#: something to read before knowing what the question is. They moved to
#: :data:`PLATFORM_GUIDE_DIR` and are routed from its README.
INSTRUCTION_FILES: tuple[str, ...] = (
    "house_rules.md",
    "investigation.md",
    "integration_model.md",
    "writing_the_docs.md",
    "building_topology.md",
)

#: Read before anything else, and deliberately short. Everything else in the
#: workspace is opened on demand. Named here rather than in the guide or the
#: provider prompt so the three cannot disagree about what "start here" means.
BOOTSTRAP_FILES: tuple[Path, ...] = (
    AGENT_GUIDE,
    GOAL,
    INDEX,
    INSTRUCTIONS / "house_rules.md",
)

#: On-demand reference: the scan's own field vocabulary and the verified
#: platform method, one file per guide, with a README that says what each one
#: answers. Staged separately from the five core files so the bootstrap does
#: not grow every time the method does.
PLATFORM_GUIDE_DIR = INSTRUCTIONS / "platform"
PLATFORM_GUIDE_INDEX = PLATFORM_GUIDE_DIR / "README.md"

#: The scan-shape guide. Named here because the router, the guide and the
#: tests all have to agree on it, and because it is the one guide that is not
#: a copy of an upstream method file.
READING_THE_SCAN_GUIDE = "reading_the_scan.md"

#: Written by the agent when it has a topology to express, and compiled by
#: ``topology.py``. Absent is a legitimate outcome, not a failure.
TOPOLOGY_FILE = OUTPUT / "topology.json"

#: The agent's own progress notes. AGENT-OWNED: nothing in Nexus may write to
#: this path. The runner reads a bounded tail of it when composing a restart
#: prompt and leaves it exactly as the agent left it.
#:
#: It used to be both halves. The runner read it, composed a restart summary
#: that EMBEDDED it, and then wrote that summary back over the same file -- so
#: three restarts produced a file containing three nested copies of itself and
#: none of the agent's own record of what was left to do.
AGENT_PROGRESS = NOTES / "progress.md"

#: Nexus-owned control state, and the counterpart to the rule above. Under an
#: underscore-prefixed directory on purpose: ``publish._is_visible`` and
#: ``is_published_doc_filename`` both reject such a segment, so control state
#: cannot reach a client document by being forgotten in a cleanup list.
CONTROL = Path("_agent_control")

#: The between-attempt summary, written by the runner and read by nothing else.
#: It is passed to the provider through ``AgentRunRequest.checkpoint_summary``;
#: the file exists so a crashed run leaves the operator the same text.
RUNNER_CHECKPOINT = CONTROL / "checkpoint.md"

#: Binds ``output/``, ``notes/`` and the integration model to the run that
#: produced them. Reused state that cannot prove it belongs here is discarded,
#: never merged.
RESUME_IDENTITY = CONTROL / "resume_identity.json"

# The provider-neutral shared knowledge record written by the lead agent and
# reused by restarts, catalogue assembly, and topology compilation.
INTEGRATION_MODEL = NOTES / "integration_model.json"

#: One integration per file, and the preferred shape. The single
#: ``integration_model.json`` above still loads -- a resume must not lose an
#: older run's record -- but the agent is taught to write here.
#:
#: VERIFIED FAILURE (run 252): the agent hand-wrote 15 KB of nested JSON with
#: no tool in the sandbox to parse it, omitted one ``"steps": [`` on line 17,
#: and the whole investigation became unreadable -- 5 integrations and 6
#: journeys lost to one bracket. It had no way to know. Per-item partial
#: acceptance did not help, because it only runs once ``json.loads`` has
#: succeeded on the WHOLE file.
#:
#: One file per record bounds the blast radius to the record, which is the
#: same reasoning that made the coverage log JSONL -- and the coverage log
#: came through that run intact at 142 of 142 while the model did not.
INTEGRATION_DIR = NOTES / "integrations"

#: The analysis layer, and the reason it exists.
#:
#: MEASURED (run 252): the agent read all four bootstrap files, all five
#: instruction files and four platform guides -- roughly 21 KB of method --
#: then classified 142 designs in about 90 seconds having opened SIX design
#: files, and wrote documents straight from the index. Depth guidance was not
#: missed; it was read and it did not change the behaviour.
#:
#: The predecessor Claude Code workflow produced markedly deeper output, and
#: the mechanism was not a bigger prompt: it required one analysis file per
#: group before any document could be written, so the prose layer could not be
#: reached without passing through the evidence layer.
#:
#: This restores that obligation WITHOUT restoring the orchestrator. There is
#: still no phase driver, no assigned worker, no batch and no ordering imposed
#: by Nexus. The agent chooses when to write these, in what order, and whether
#: to fan out. It simply cannot claim completion without them -- the same
#: shape as the coverage ledger, which is the part of run 252 that worked.
ANALYSIS_DIR = NOTES / "analysis"


# --------------------------------------------------------------------------
# The accountability seam
#
# "The agent is free to choose how it investigates, but it is not free to
# choose what must be accounted for." These names are the machine-readable
# half of that sentence, and they live here because FOUR modules have to agree
# on them -- the ledger writes them, the completion check reads them, the
# accountability gate judges them, and the instructions teach them. Any one of
# the four carrying its own copy is how the gate and the prompt end up
# describing different vocabularies, which reads to the agent as an arbitrary
# refusal.
#
# THE ROSTER IS A CONVENIENCE, NEVER THE OBLIGATION. It lives under
# agent-writable ``notes/``, so deleting a row from it must not shrink what the
# run owes. The gate recomputes the population from ``facts/navigation/units/``
# -- inside ``EVIDENCE_ROOTS``, therefore inside the integrity digest -- and
# compares the agent's dispositions against THAT. This is the same class of bug
# as trusting an approval receipt to name its own allowed roots: a control file
# the controlled party can edit controls nothing.
# --------------------------------------------------------------------------

#: Agent-writable, under ``notes/``. Seeded by Nexus, extended by the agent.
COVERAGE_DIR = NOTES / "coverage"

#: One JSON object per line, one line per parsed design, written by the builder
#: before the agent starts. The agent reads it to know what exists; it is not
#: required to keep it intact.
COVERAGE_ROSTER = COVERAGE_DIR / "roster.jsonl"

#: APPEND-ONLY. The agent adds one line per classification decision. Append
#: rather than rewrite because a 142-line JSON object rewritten after every
#: discovery is a file that loses an investigation to one interrupted write --
#: and because the last line for a candidate winning makes revision natural
#: instead of a read-modify-write the agent has to get right.
COVERAGE_LOG = COVERAGE_DIR / "classifications.jsonl"

#: The agent's completion claim (plan section 7). Its ABSENCE is the normal
#: case for an unfinished run and means exactly that: the session stopped. A
#: provider process exiting zero is not a claim that the goal was met, and
#: treating it as one is the specific defect that published run 250.
COMPLETION_RECEIPT = NOTES / "completion.json"

#: Every state a parsed design may end in. Closed, and ordered from most to
#: least work owed. ``unresolved`` is deliberately a legitimate answer: an
#: investigation that cannot settle a design must be able to say so, and the
#: run then reports incomplete rather than the agent inventing a verdict to
#: clear the gate.
CandidateDisposition = Literal[
    "material",
    "shared-component",
    "operational",
    "utility",
    "test-or-obsolete",
    "excluded",
    "unresolved",
]

#: Dispositions that REDUCE what the run owes, and therefore cost a sentence of
#: justification. ``material`` and ``shared-component`` are the two that assert
#: work was done and are free; every other value ends an obligation, and an
#: obligation ended without a reason is an escape hatch. The reason is checked
#: for existence and length only -- never for wording, which would be exactly
#: the brittle prose matching this lane exists to avoid.
DISPOSITION_REQUIRES_REASON: frozenset[str] = frozenset(
    {"operational", "utility", "test-or-obsolete", "excluded", "unresolved"}
)

#: Dispositions that assert this design participates in documented integration
#: behaviour, and therefore must name the integration that covers it. The link
#: is carried BY THE CLASSIFICATION as an integration id, not inferred from the
#: model.
#:
#: This is why: ``integration_model.ImplementingUnit`` has ``id``, ``label``,
#: ``unit_kind``, ``platform`` and ``project_ref`` -- no scanner unit id. So
#: matching a parsed design to its integration through the model could only be
#: done on the LABEL, and plan section 8 is explicit that nothing may fail
#: "because prose uses a slightly different job name". The classification line
#: already holds the authoritative ``unit_id`` (it was seeded from the roster);
#: the agent adds the integration id, and the gate compares two ids for exact
#: equality. No name is ever compared.
DISPOSITION_REQUIRES_MODEL_LINK: frozenset[str] = frozenset({"material"})


# --------------------------------------------------------------------------
# Navigation
#
# `INDEX.md` and `facts/navigation/` are a materialized VIEW over the canonical
# parsed JSON, not a second source of truth. They exist because the evidence of
# record is sharded megabytes and an agent cannot find its way around it with
# `glob` alone -- and because the predecessor's answer to the same problem was a
# 38 MB fact graph handed over as the primary interface.
#
# Two rules make a view rather than another blob:
#
# * **Rows are complete on ONE line.** JSONL, not JSON, so `grep` returns a
#   usable record instead of a fragment of an object spread over 40 lines.
# * **Splitting is by BYTES, never by row count.** A small estate produces one
#   shard and a large one produces as many as it needs; nothing here has a
#   fixture-derived size in it.
# --------------------------------------------------------------------------

NAVIGATION = FACTS / "navigation"

#: Written beside the shards. Names every view, its shards in order, its row
#: count and the canonical artifact each row resolves back to.
NAVIGATION_MANIFEST = NAVIGATION / "manifest.json"

#: Byte ceiling for one navigation shard. Chosen to stay far below the
#: readability ceiling so a shard can be read whole when grep is not enough.
NAVIGATION_SHARD_BYTES = 400_000

#: The views the builder renders, in the order the index introduces them.
#: Every name is a directory under :data:`NAVIGATION` holding ``part_NNN.jsonl``
#: shards; a view with no rows on this estate is absent, and the manifest says
#: so rather than leaving an empty directory to be read as "nothing exists".
NAVIGATION_VIEWS: tuple[str, ...] = (
    "units",
    "dependencies",
    "routine_usage",
    "interfaces",
    "channel_pairings",
    "mappings",
    "configuration",
)


# --------------------------------------------------------------------------
# The source boundary
#
# The scanner is the trusted parser. The documentation agent is not, and must
# never receive original or verbatim source code -- not as a file, and not
# embedded in parsed JSON, which is the hole this constant closes.
#
# The distinction that matters, and the one the tests have to hold apart:
#
# * A **mapping expression** is a structured parser fact. It is extracted per
#   field, verified by the same redaction pass as everything else, and it IS
#   the field-level documentation. It stays.
# * A **code body** -- a tJava block, a routine's source -- is source text that
#   happened to be carried inside the parse. It goes, and the projection states
#   that it was withheld rather than leaving the reader to infer absence.
#
# Removing the second one is a real cost: business rules hide in custom code,
# and a derived summary is weaker evidence than the body. It is the cost the
# security boundary was accepted for, and `facts/parser_limitations.md` is
# where a document learns to disclose it instead of denying it.
# --------------------------------------------------------------------------

#: Keys holding a verbatim code body on a parsed design or routine. Removed by
#: the projection and replaced by a stated, derived summary.
VERBATIM_CODE_FIELDS: tuple[str, ...] = ("code", "source_code")

#: Component parameters whose VALUE is a code body rather than a setting.
#: Mirrors ``intodoc.models.Component.CODE_PARAM_NAMES``. Duplicated rather
#: than imported because ``intodoc`` is a separate distribution and is NOT
#: installed in the MCP server's environment -- there is no import to share.
#: A scanner-side addition here would be staged verbatim until this tuple
#: catches up, so the duplication is pinned by a static comparison against the
#: scanner's source rather than trusted (``tests/test_agent_workspace``).
CODE_PARAM_NAMES: tuple[str, ...] = (
    "CODE_START",
    "CODE",
    "CODE_MAIN",
    "CODE_END",
    "CODE_ONCE",
)

#: Every filesystem-writing tool the runtime exposes, NOT just the one we
#: happen to think of. A provider offers a family, a model picks whichever it
#: prefers, and the policy is deny-by-default -- so naming one member silently
#: denies the rest.
#:
#: CORRECTION, run 239: OpenCode maps the ``apply_patch`` TOOL onto the
#: ``edit`` PERMISSION -- it reported the blocking rule as
#: ``{"permission":"edit","pattern":"*","action":"deny"}`` while the agent was
#: calling ``apply_patch``. So naming the family is NOT what unblocked writes;
#: the pattern forms in :func:`writable_globs` were. These entries are kept
#: because they cost nothing and a future runtime may well split the
#: permissions apart, but do not mistake them for the fix, and do not assume a
#: tool and its permission share a name.
#:
#: Run 237 is still the reason this list exists: eight ``tool apply_patch
#: error`` lines, four minutes of sub-agents, document text produced, and
#: ``output/`` empty at publication. The test that should have caught it
#: asserted ``permissions["edit"]`` was correctly scoped -- which it was, and
#: which was beside the point twice over.
#:
#: Names confirmed against the shipped binary
#: (``opencode-ai@1.18.7``, ``bin/opencode.exe``): ``apply_patch`` 44 quoted
#: occurrences, ``patch`` 61, ``write`` 61, ``edit`` 78,
#: ``str_replace_editor`` 10, ``multiedit`` 0. Re-check on a runtime upgrade;
#: a new write tool would be denied by default and look exactly like this bug.
WRITE_TOOLS: tuple[str, ...] = (
    "edit",
    "write",
    "patch",
    "apply_patch",
    "str_replace_editor",
)

#: Read, search, write and delegate. ``task`` is present so an agent may fan
#: out when it judges that useful -- nothing in this lane sizes, counts or
#: assigns those sub-agents. ``todowrite`` is the runtime's own planning
#: scratchpad and touches no file. ``bash``, ``webfetch``, ``websearch`` and
#: ``external_directory`` are absent and must stay absent: the sandbox
#: argument for allowing direct writes depends on the agent having no way out
#: of the workspace.
DEFAULT_TOOLS: tuple[str, ...] = (
    "read",
    "glob",
    "grep",
    "list",
    "task",
    "todowrite",
    *WRITE_TOOLS,
)


def writable_globs(workspace: Path) -> dict[str, str]:
    """Writes allowed anywhere in the workspace, because path scoping does not work here.

    MEASURED, not assumed. Four configurations were run against the real
    ``opencode-ai@1.18.7`` binary with a one-line "write hello to
    output/probe.md" prompt. The tool reports
    ``input.filePath = /tmp/permtest/output/probe.md`` (absolute), and:

    | ``edit`` allow beside ``{"*": "deny"}``  | result   |
    |------------------------------------------|----------|
    | ``output/*`` + ``output/**``             | BLOCKED  |
    | ``/tmp/permtest/output/*`` + ``/**``     | BLOCKED  |
    | ``/tmp/permtest/output/probe.md`` (exact)| BLOCKED  |
    | ``**``                                   | WRITTEN  |

    So no path-scoped rule -- relative, absolute, or an exact full path --
    overrides the base deny for ``edit``. Only ``**`` does. Runs 237, 239 and
    240 were all this, and three rounds of pattern-guessing cost ~25 minutes
    of model time before a three-second probe settled it.

    The legacy lane never hit this because it granted exact paths for a
    permission the model reached through a different tool, and because its
    agents wrote through a submission facade rather than the filesystem.

    **The boundary therefore moves from prevention to detection**, and lands
    somewhere it can actually be enforced:

    * ``--dir`` plus ``external_directory: deny`` keeps writes inside the
      staging tree, which is a throwaway copy that Phase-4 deletes.
    * ``bash: deny`` means there is no shell to reach anything else with.
    * Publication reads ONLY ``output/**``. Anything written elsewhere is
      never published -- not because it is deleted, but because
      ``finalize_publication`` assembles a NEW directory from the accepted
      in-memory snapshot plus an explicit Nexus-owned allowlist. A file it
      does not know about is absent by construction. It used to delete a list
      of known roots in place and publish whatever remained, which shipped
      ``rogue.md`` at the staging root.
    * The evidence roots are re-hashed after the run and compared against the
      builder's receipt (:func:`workspace.evidence_digest`). Tampering with
      the scan, the facts or the instructions fails publication.

    That last check is strictly stronger than the permission rule was
    supposed to be: a path glob would have stopped a write it recognised,
    whereas the digest catches any modification by any means, including one
    the permission layer would have allowed.
    """

    del workspace  # kept in the signature; see below for why it is unused
    return {"*": "deny", "**": "allow"}


def write_permissions(workspace: Path) -> dict[str, dict[str, str]]:
    """The same allow-map for EVERY write tool, keyed by tool name.

    Call this rather than assigning ``writable_globs`` to one permission key.
    The whole family gets identical scoping, so which tool a model reaches for
    stops being a correctness variable -- it is the difference between an
    agent that writes documentation and one that spends its budget bouncing
    off a wall it cannot see.
    """

    allowed = writable_globs(workspace)
    return {tool: dict(allowed) for tool in WRITE_TOOLS}


# --------------------------------------------------------------------------
# Run contract
# --------------------------------------------------------------------------

AgentRunStatus = Literal["completed", "incomplete", "cancelled", "failed"]


class AgentRunRequest(BaseModel):
    """Everything a provider adapter needs, and deliberately nothing more.

    An adapter's whole job is mechanical: mount the workspace, apply the
    permissions, invoke the provider, capture usage, translate provider-specific
    errors. Any field that would let this object describe *what the analysis
    should conclude* belongs in ``instructions/``, on disk, where the agent can
    read it and a human can review it.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1, max_length=200)
    project_id: str = Field(min_length=1, max_length=200)
    workspace_path: Path
    goal_path: Path
    guide_path: Path
    #: The compact navigation map. Part of the bootstrap, so the adapter's
    #: pointer-prompt can name it without knowing the layout.
    index_path: Path
    output_path: Path
    scratch_path: Path
    allowed_tools: tuple[str, ...] = DEFAULT_TOOLS
    ceiling: ResourceCeiling = Field(default_factory=ResourceCeiling)
    #: Present only on a restart. Plain prose describing what already exists in
    #: ``output/`` and what is left, so a fresh session does not redo finished
    #: work. Never a machine-readable state blob -- if it needs a schema, it is
    #: reintroducing the contract this lane exists to remove.
    checkpoint_summary: str | None = None
    #: Free-form, adapter-local, never domain content (a model route, a config
    #: path). An adapter that needs domain input here is mis-designed.
    adapter_options: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _paths_are_inside_the_workspace(self) -> AgentRunRequest:
        root = self.workspace_path.resolve()
        for label in (
            "goal_path",
            "guide_path",
            "index_path",
            "output_path",
            "scratch_path",
        ):
            path: Path = getattr(self, label)
            try:
                path.resolve().relative_to(root)
            except ValueError as exc:
                raise ValueError(f"{label} must be inside workspace_path") from exc
        return self

    @classmethod
    def for_workspace(cls, workspace: Path, **kwargs: object) -> AgentRunRequest:
        """Build a request from a workspace root, so callers cannot mis-wire it."""

        return cls(
            workspace_path=workspace,
            goal_path=workspace / GOAL,
            guide_path=workspace / AGENT_GUIDE,
            index_path=workspace / INDEX,
            output_path=workspace / OUTPUT,
            scratch_path=workspace / NOTES,
            **kwargs,  # type: ignore[arg-type]
        )


class AgentRunResult(BaseModel):
    """What the adapter observed. Not a judgement about the documentation."""

    model_config = ConfigDict(extra="forbid")

    status: AgentRunStatus
    provider_id: str
    model: str
    usage: UsageReceipt = Field(default_factory=UsageReceipt)
    #: Advisory. Useful for logs and for a same-session retry, but nothing in
    #: this lane may require it to be non-null in order to make progress.
    session_id: str | None = None
    stop_reason: str | None = None
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.status == "completed"

    @property
    def session_was_lost(self) -> bool:
        """True when the failure looks like a vanished provider session.

        The verified case is OpenCode exiting 1 with ``Error: Session not
        found`` after a valid artifact had already been written. That is a
        transport fault; retrying with a fresh session is correct, and it is
        the runner -- not the adapter -- that decides to do so.
        """

        text = f"{self.error or ''} {self.stop_reason or ''}".casefold()
        return "session not found" in text or "session expired" in text


@runtime_checkable
class AgentProvider(Protocol):
    """One call. No phases, no batches, no assigned units."""

    provider_id: str

    async def run_agent(self, request: AgentRunRequest, *, log_cb: object) -> AgentRunResult: ...


# --------------------------------------------------------------------------
# Builder seam
#
# The builder and the runner are written independently. This is the only
# surface between them, so it is declared here rather than inferred from
# whichever module happens to get written first.
# --------------------------------------------------------------------------


class WorkspaceReceipt(BaseModel):
    """What the builder actually staged, for the run log and the dry run.

    Counts, not content. The point is that an operator reading a log can tell
    an empty workspace from a full one without opening it, and that the dry
    run has something to assert against.
    """

    model_config = ConfigDict(extra="forbid")

    project_id: str
    platform: str
    workspace_path: Path
    design_count: int = Field(ge=0)
    group_count: int = Field(ge=0)
    mapping_table_count: int = Field(ge=0)
    instruction_files: tuple[str, ...] = ()
    #: Platform/task guides staged under ``instructions/platform/``, routed
    #: from its README and opened on demand rather than at startup.
    platform_guides: tuple[str, ...] = ()
    #: Rows per navigation view. An absent view is a view with no rows on this
    #: estate, which is a fact about the estate and belongs on the receipt.
    navigation_rows: dict[str, int] = Field(default_factory=dict)
    #: Rows per searchable index (``index/*.jsonl``). Reported separately from
    #: ``navigation_rows`` because they answer different questions and a thin
    #: one is diagnostic: zero ``relationships`` on a real estate means the
    #: dependency extraction produced nothing, which looks identical in a
    #: finished document to an estate whose designs genuinely call nothing.
    index_rows: dict[str, int] = Field(default_factory=dict)
    #: Rows compiled into the client-facing deterministic reference shelf.
    #: These are output counts, not agent quotas.
    reference_registers: dict[str, int] = Field(default_factory=dict)
    #: Parsed designs seeded into the coverage roster: the size of the
    #: population the run is accountable for. NOT a document target and NOT a
    #: work plan -- several designs routinely belong to one integration, and
    #: many are classified in a line rather than investigated. It is on the
    #: receipt so the run log states the denominator before the agent starts,
    #: which is what "6 of 142 designs opened" needed and did not have.
    coverage_candidates: int = Field(default=0, ge=0)
    #: ``True`` when a runtime mirror supplied evidence. ``False`` is normal and
    #: means ``runtime/`` states its own absence -- never that it is missing.
    runtime_available: bool = False
    #: Digest of the staged tree, for reingestion and for proving byte-stability
    #: between two builds of an unchanged scan.
    content_digest: str = ""
    #: Non-fatal observations from staging (an absent optional artifact, a
    #: skipped empty directory). Fatal problems raise instead.
    notices: tuple[str, ...] = ()


#: Which part of the lane failed. Carried on the error rather than inferred
#: from its message, because an operator's first question is always "where",
#: and because a message is prose that gets reworded.
WorkspaceStage = Literal[
    "approval",
    "projection",
    "navigation",
    "reference",
    "instructions",
    "runtime",
    "publication",
    "topology",
]


class WorkspaceUnavailableError(RuntimeError):
    """The workspace cannot be built or published safely; stop before any model call.

    Raised for a missing or unapproved ``publication_approval.json``, a digest
    mismatch, a refused symlink, or an absent scan. Deliberately fail-closed and
    with no degraded path: every alternative to stopping here publishes
    unredacted or unverified client material.

    **One family, every stage.** Navigation, instruction rendering and
    deterministic reference compilation raised their own unrelated types, so a
    failure in any of them escaped the runner's single ``except`` and reached
    the operator as a bare "Generation failed". Subclass this for a new stage
    rather than introducing a sibling.

    The message is client-safe by construction: it may name a relative artifact
    path and a stage, never a file's contents.
    """

    def __init__(self, message: str, *, stage: WorkspaceStage = "approval") -> None:
        super().__init__(message)
        self.stage: WorkspaceStage = stage

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"[{self.stage}] {super().__str__()}"


@runtime_checkable
class WorkspaceBuilder(Protocol):
    """Implemented by ``workspace.build_workspace``; called by ``runner.py``."""

    def __call__(
        self,
        *,
        project_id: str,
        staging_dir: Path,
        platform: str,
        goal: str,
        #: ``"whole_estate"`` or ``"focused"``, DECLARED by the operator. Typed
        #: as ``str | None`` rather than importing ``guide.ScopeMode``: this
        #: module is the bottom of the lane and imports nothing from it.
        #: ``None`` is undeclared and must resolve to the wider obligation.
        scope_mode: str | None = None,
    ) -> WorkspaceReceipt: ...
