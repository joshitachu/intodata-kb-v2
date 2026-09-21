"""Per-conversation OpenCode workspace for the labs lane.

One directory per conversation holds everything OpenCode is allowed to see:

    <LABS_WORKSPACE_ROOT>/conversation-<id>/
        opencode.json   -- provider, model, MCP endpoint + bearer, deny-by-default policy
        AGENTS.md       -- the guidance stack, then standing instructions and retrieval order
        .nexus/         -- the guidance stack on disk, one directory per level
        output/         -- the one writable path; deliverables the agent hands the user
        docs -> <LABS_DOCS_ROOT>/<project_id>/docs   (symlink, published docs only)

The workspace is the containment boundary for file access: OpenCode's session is
bound to this directory, the only writable path in it is `output/`, and the only
project material reachable from here is the published documentation the symlink
exposes. Everything else -- runtime evidence and metadata -- comes back
through the Nexus MCP bridge, which re-derives RBAC from the bearer token.

`output/` is where a request the lane could not previously answer lands: asked
for "a C4 diagram as a markdown file", the agent used to be able to generate the
content and then have nowhere to put it. It is scoped by path rather than opened
generally -- see :func:`_edit_permission` for why that scoping is expressed on
the `edit` key and why it is spelled once per possible worktree.

`.nexus/` holds the same guidance that `AGENTS.md` opens with, split one file per
level, and is a read path rather than new authority: a level the model wants to
re-read verbatim costs a `read` inside the boundary instead of a tool call, and
the split is what makes "which level said this?" answerable at all once the four
render as one block. Nothing is written back to it -- learned guidance goes to
the database through the repository, never to a file the agent can see change.

`.nexus/project/` additionally carries the project's uploaded material: the
manifest of what exists, and the full text of every document small enough and
authoritative enough to be read rather than searched (see
:mod:`src.harness.guidance.material` for why role and size decide that
independently). These files are extracted copies, not a second store: the
document rows remain the original, and the copy exists so a constraint the answer
must respect is *in front of* the agent instead of one similarity query away.
Which means they must also disappear the moment they stop being true -- a mounted
policy that was deleted, re-roled or grown past the ceiling is worse than no
policy, because the agent will read it and cite it with full confidence.

`.nexus/project/generation/` carries the same idea applied to a second source:
the documentation pipeline's own memory for this project -- its vocabulary, its
known limitations, the corrections a person made and the questions it could not
settle. Same route, same rules, same sweep; the classification and the
staleness policy are :mod:`src.labs.generation_memory`, and the reason it is
mounted rather than written back is the paragraph above. It is an extract, so
it disappears by the identical mechanism: a file that was emptied, deleted or
grown past the ceiling stops being projected and is removed.

The layout itself -- which files exist and what is in each -- is
:func:`project_workspace_files`, a pure projection with no filesystem in it.
:func:`ensure_labs_workspace` writes that projection and sweeps anything outside
it; the read-only API that shows a user their own worktree projects the same
inputs and writes nothing. Having the writer own the layout privately is what
made the workspace unshowable in the first place, and a second description of it
elsewhere would have drifted from the real one within a release.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from src.harness.guidance.material import (
    MaterialEntry,
    MaterialKind,
    ProjectMaterial,
    workspace_relpath,
)
from src.harness.guidance.models import GuidanceLayer, ResolvedGuidance, estimate_tokens
from src.harness.guidance.renderer import render_guidance, render_layer, render_manifest
from src.harness.models import HarnessRiskTier
from src.harness.registry import base_capability_registry
from src.labs.contracts import LabsTurnMode
from src.labs.generation_memory import (
    MEMORY_DIRNAME,
    GenerationMemory,
    render_memory_section,
)

logger = logging.getLogger(__name__)

_DEFAULT_WORKSPACE_ROOT: Final = "/tmp/nexus-labs-workspaces"
# Local dev default. The backend and the OpenCode server run on the same host in
# the spike, so 127.0.0.1 is what actually resolves. When OpenCode runs inside a
# container it cannot reach the host loopback: set
# LABS_MCP_URL=http://host.docker.internal:8000/api/v1/labs/mcp there.
_DEFAULT_MCP_URL: Final = "http://127.0.0.1:8000/api/v1/labs/mcp"

# The lane's model, overridable without a rebuild.
#
# Default is OpenCode Zen's `big-pickle`, which needs no provider credential at
# all -- unlike the `*-free` models, which list fine but fail with UnknownError
# until an OpenCode account is logged in. Set LABS_PROVIDER_ID=moonshot and
# LABS_MODEL_ID=kimi-k2.6 to put the lane back on the model `/chat` uses, which
# is the only configuration where a quality difference isolates the runtime
# rather than the model.
_MODEL_ID: Final = os.environ.get("LABS_MODEL_ID", "").strip() or "big-pickle"
_PROVIDER_ID: Final = os.environ.get("LABS_PROVIDER_ID", "").strip() or "opencode"
_AGENT_NAME: Final = "nexus"

#: The read-only companion agent. Named `plan` deliberately: that is the name a
#: turn selects with `agent="plan"`, and OpenCode ships a built-in agent under
#: the same key whose fields this definition overrides rather than shadows.
_PLAN_AGENT_NAME: Final = "plan"

#: The verification subagent. Not selectable by a user -- `mode: "subagent"` --
#: and reachable only by the `nexus` agent spawning it mid-turn.
_VERIFY_AGENT_NAME: Final = "verify"

#: OpenCode's own subagents, which exist whether this config mentions them or
#: not. Redefined here so that admitting `task` for one agent cannot become a
#: route to an unbounded, unconstrained loop: each is re-declared read-only, with
#: spawning denied, so the worst case of a mis-scoped `task` permission is a
#: harmless read rather than a second agent with the default tool set.
_BUILTIN_SUBAGENTS: Final = ("general", "explore")

# Escape-hatch tools, denied by name. `write`, `edit` and `patch` are absent
# because none of them is a permission key: OpenCode routes all three onto
# `edit`, which _edit_permission below owns. Listing them here would have read
# as three denials that in fact resolve to nothing.
#
# `task` used to be here. It is now `_SPAWN_TOOL`, still denied everywhere by
# default and opened for exactly one agent against exactly one subagent -- see
# `_verify_agent`.
_DENIED_TOOLS: Final = ("bash", "webfetch")
_ALLOWED_TOOLS: Final = ("read", "list", "glob", "grep")

#: The tool that spawns a subagent.
#:
#: Denied in `_base_permission`, so the floor for every agent OpenCode ships --
#: and for the `plan` agent -- is still "cannot spawn anything". The `nexus`
#: agent alone re-opens it, pattern-scoped to the verification subagent, and
#: every subagent this workspace defines denies it again. That last part is the
#: recursion bound, and it is structural: a subagent whose own tool map sets
#: `task: false` cannot spawn regardless of what its prompt says or what the
#: model decides to try.
_SPAWN_TOOL: Final = "task"

#: The tool the agent publishes its plan through.
#:
#: Named on its own because the runtime reads its ARGUMENTS: a `todowrite` call
#: carries the whole step list, and `runtime._BusStream` forwards that list on a
#: `plan.updated` frame so the client can render the plan as a discrete card.
#: That is the only route a plan has to the user. It used to travel as chat prose
#: -- the prompt asked for a spoken plan and the model narrated every step into
#: the answer stream, which put thirteen paragraphs of "I see the Mediq branch
#: but not yet where it splits" above one turn's answer, internal component ids
#: and all.
_PLAN_TOOL_WRITE: Final = "todowrite"

#: The agent's own plan, as a tool it maintains rather than prose it forgets.
#:
#: These were reachable before only because no `"*": "deny"` catch-all exists, so
#: they were admitted by accident and advertised to nobody. Naming them makes the
#: plan a real artifact: `todowrite` publishes a `plan.updated` frame the client
#: renders as a plan card, so the user watching a long turn sees the agent working
#: through a list instead of a spinner.
_PLAN_TOOLS: Final = (_PLAN_TOOL_WRITE, "todoread")

#: The one directory in the workspace the agent may write to.
_OUTPUT_DIR: Final = "output"

#: The procedures the lane ships, by directory name under
#: `labs/opencode/skills/`. Named here so the `skill` permission can admit these
#: and nothing else: OpenCode's built-in `customize-opencode` skill would
#: otherwise be advertised too, and it teaches a client-facing assistant to edit
#: the config of the runtime it is running inside.
_SKILLS: Final = ("estate-review", "onboarding", "pack-building", "runtime-investigation")

#: How the MCP bridge's server key prefixes every Nexus tool name the model sees.
_MCP_SERVER_KEY: Final = "nexus"

#: Nexus tool-name prefixes that only ever read.
#:
#: The plan agent denies every `nexus_*` tool and then re-admits these. Written
#: as prefixes rather than as a list of names so a capability added later is
#: admitted only if it is named like a read -- a new `create_*` or `generate_*`
#: capability stays denied without anyone remembering to come back here.
_PLAN_READ_PREFIXES: Final = ("read", "list", "search", "query", "get")


def _consequential_tool_names() -> tuple[str, ...]:
    """Every Nexus tool name that is not read-only, derived from the registry.

    The plan agent must not be able to create, save, trigger or cancel
    anything. A hand-written list of tool names to deny would be correct on the
    day it was written and quietly wrong the first time a capability is added,
    so it is computed from the same registry the MCP bridge advertises from.

    The projection mirrors the bridge's: `semantic_name` if the capability has
    one, otherwise its id with the characters MCP forbids in a tool name
    replaced. Kept as a belt to the `nexus_*` braces below -- if a future
    release stops honouring wildcard permission keys, these exact names still
    deny every consequential capability that exists today.
    """

    names: set[str] = set()
    for capability in base_capability_registry().list():
        if capability.risk_tier is HarnessRiskTier.READ_ONLY:
            continue
        raw = capability.semantic_name or capability.id
        names.add(f"{_MCP_SERVER_KEY}_{re.sub(r'[^a-zA-Z0-9_-]', '_', raw)}")
    return tuple(sorted(names))


#: Computed once, at import: a registry that fails to build should break the
#: process, not a turn -- the same argument `_material_roots` makes below.
_CONSEQUENTIAL_TOOLS: Final[tuple[str, ...]] = _consequential_tool_names()

_NEXUS_DIR: Final = ".nexus"

#: The project level's directory, named once. `_LAYER_FILES` below spells the
#: same segment inside its relative path because that tuple is read as a literal
#: map of the tree; everything the material sweep touches is built from this.
_PROJECT_DIR: Final = "project"

#: The on-disk copy of the manifest that `AGENTS.md` already carries.
#:
#: Duplication with a purpose: `AGENTS.md` is always in context and therefore
#: always costs tokens, while this file costs nothing until something opens it.
#: A `grep` for a document title, or a re-read after a long tool sequence has
#: pushed the head of the context out of the model's attention, both land here.
_MANIFEST_FILE: Final = "MANIFEST.md"

#: The workspace's entry point, and the only file whose content the model is
#: handed unprompted.
_AGENTS_FILE: Final = "AGENTS.md"

#: What produced a projected file. Strings rather than an enum because one of
#: them carries a document id, and a UI that has to branch on "enum member or
#: formatted string" is worse off than one that matches a documented prefix.
#:
#: * ``assembled``      -- `AGENTS.md`, built from everything below it.
#: * ``system_prompt``  -- the agent's system role, defined in code.
#: * ``org_layer`` / ``project_layer`` / ``person_layer`` -- one guidance level,
#:   rendered verbatim, the same bytes `AGENTS.md` opens with.
#: * ``thread``         -- the thread level, which has no content of its own.
#: * ``manifest``       -- the list of what the project holds.
#: * ``document:<id>``  -- an extracted copy of one uploaded document.
#: * ``generation:<file>`` -- an extract of the documentation run's own memory.
#:   Distinct from ``document:`` because the provenance is genuinely different
#:   and a user auditing their workspace has to be able to tell "someone at
#:   this client uploaded this" from "our pipeline wrote this about them".
ORIGIN_ASSEMBLED: Final = "assembled"
ORIGIN_ORG_LAYER: Final = "org_layer"
ORIGIN_PROJECT_LAYER: Final = "project_layer"
ORIGIN_PERSON_LAYER: Final = "person_layer"
ORIGIN_THREAD: Final = "thread"
ORIGIN_MANIFEST: Final = "manifest"
ORIGIN_DOCUMENT_PREFIX: Final = "document:"
ORIGIN_GENERATION_PREFIX: Final = "generation:"

#: Where each level of the guidance stack lands under `.nexus/`, and what to
#: call its origin.
#:
#: The filenames say what the level is for rather than repeating the directory
#: name: RULES.md is binding, PREFERENCES.md is the presentation overlay, and a
#: model that opens the wrong one should be able to tell from the name alone. The
#: PERSON level lives under `user/` because that is the word the product uses for
#: it everywhere outside the guidance contract.
_LAYER_FILES: Final[tuple[tuple[GuidanceLayer, str, str], ...]] = (
    (GuidanceLayer.ORG, "org/RULES.md", ORIGIN_ORG_LAYER),
    (GuidanceLayer.PROJECT, "project/RULES.md", ORIGIN_PROJECT_LAYER),
    (GuidanceLayer.PERSON, "user/PREFERENCES.md", ORIGIN_PERSON_LAYER),
)

#: The THREAD level exists in the contract and has no file to render.
#:
#: The directory is still created: an absent `thread/` reads as "this build
#: predates the thread level", while an empty one with this note reads as "the
#: history is somewhere else", which is the true statement.
_THREAD_README: Final = """# Thread

Nothing about this conversation is stored in this directory. The thread level of
the guidance stack is the conversation history itself, and that reaches you
through the session, not the filesystem: there is no transcript, no scratchpad
and no note file here to read.
"""


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def labs_workspace_path(conversation_id: int) -> str:
    """Return the workspace path for a conversation without creating anything.

    Split out because the MCP token is minted with the workspace directory inside
    its scope, while the workspace config is written with the token inside it.
    The path is a pure function of the conversation id, so it breaks that cycle.
    """

    root = Path(_env("LABS_WORKSPACE_ROOT", _DEFAULT_WORKSPACE_ROOT))
    return str((root / f"conversation-{int(conversation_id)}").resolve())


def _base_permission() -> dict[str, Any]:
    """The policy every agent in this workspace starts from.

    `edit` is deliberately absent: it is the one key that differs between the
    two agents, and each supplies its own so that neither inherits a write rule
    it did not ask for.

    `skill` is an allowlist rather than `"allow"`. OpenCode filters the
    advertised skill list through this key by skill NAME, and it ships a
    built-in `customize-opencode` skill that explains how to edit opencode's own
    configuration. Advertising that to a client-facing assistant is both a
    per-turn token cost and an invitation to go and rewrite the runtime it is
    running inside.
    """

    permission: dict[str, Any] = {tool: "allow" for tool in _ALLOWED_TOOLS}
    permission.update({tool: "allow" for tool in _PLAN_TOOLS})
    permission.update({tool: "deny" for tool in _DENIED_TOOLS})
    # Denied in the floor, re-opened by exactly one agent. Spelled here rather
    # than in `_DENIED_TOOLS` so the exception is visible at the one place that
    # makes it, instead of being a name quietly missing from a deny list.
    permission[_SPAWN_TOOL] = "deny"
    permission["websearch"] = "deny"
    permission["question"] = "deny"
    # Broad rule first, narrow rules after: OpenCode evaluates the LAST matching
    # rule, so the order of this dict is the policy, not a formatting choice.
    permission["skill"] = {"*": "deny", **{name: "allow" for name in _SKILLS}}
    # Every conversation shares the OpenCode container. Arbitrary external
    # reads would let one workspace inspect a sibling workspace (including its
    # MCP bearer) or another project's generated output. Project evidence is
    # available through the scoped Nexus bridge instead.
    permission["external_directory"] = "deny"
    return permission


def _build_opencode_config(
    *, mcp_token: str, workspace: Path, mode: LabsTurnMode = LabsTurnMode.CHAT
) -> dict[str, Any]:
    """Build the workspace's `opencode.json`.

    `mode` selects the `nexus` agent's system prompt only. It changes nothing
    about the permission blocks: what the agent may DO is identical in both
    modes, and what it may SEE is decided by the MCP bridge against the same
    mode. This file is rewritten on every turn by `ensure_labs_workspace`, which
    is what makes a per-turn mode expressible here at all.

    The `permission` block is the policy layer
    (https://opencode.ai/docs/permissions/: every tool key takes
    "allow" | "ask" | "deny" or an ordered `{pattern: action}` map, `*` is the
    catch-all, and agent-level blocks merge over the global one). No key is ever
    set to `"ask"`: there is no approval surface on this lane, so an `ask` would
    park the turn rather than prompt anyone.

    The agent `tools` map is kept, with its claim corrected. It is NOT an
    independent second enforcement layer on 1.18.7: the agent decoder turns each
    entry into a `{permission: key, pattern: "*"}` rule and then lets the agent's
    own `permission` block overwrite any key the two share. It survives because
    it is the only place that can name a tool the permission block does not --
    `nexus_*` and the consequential capability names below -- and because it
    still says the right thing if a release restores tool-list filtering.

    Three decisions, each resolved toward "the lane works, and it cannot write
    outside one directory":

    * A `"*": "deny"` catch-all is NOT used. The wildcard covers MCP tool ids
      too -- OpenCode's own `explore` agent uses exactly that to switch
      everything off -- so a catch-all here would deny every `nexus_*` call, the
      one thing this lane exists to exercise. Escape-hatch keys are denied by
      name instead.
    * `external_directory` is denied. Published documents are reached through
      Nexus's scoped RAG/MCP tools instead of a symlink into the shared output
      tree, so one conversation cannot read another conversation's workspace,
      bearer-bearing config, or another project's generated files.
    * Writes are path-scoped rather than denied outright, so the agent can
      actually hand a user a file. See `_edit_permission`.
    """

    permission = _base_permission()
    # The global block is the floor for every agent OpenCode also ships
    # (`build`, `general`, `explore` and the hidden title/summary agents). None
    # of them is selectable on this lane, and none of them gets a write rule:
    # only the `nexus` agent below does.
    permission["edit"] = "deny"

    agent_tools: dict[str, bool] = {tool: True for tool in _ALLOWED_TOOLS}
    agent_tools.update({tool: True for tool in _PLAN_TOOLS})
    agent_tools.update({tool: False for tool in _DENIED_TOOLS})
    # The one agent that may spawn, and only into `_VERIFY_AGENT_NAME`.
    agent_tools[_SPAWN_TOOL] = True

    nexus_permission = _base_permission()
    nexus_permission["edit"] = _edit_permission(workspace)
    # Broad rule first, narrow rule after -- OpenCode evaluates the LAST matching
    # rule, so reversing these two would admit every subagent instead of one.
    nexus_permission[_SPAWN_TOOL] = {"*": "deny", _VERIFY_AGENT_NAME: "allow"}

    return {
        "$schema": "https://opencode.ai/config.json",
        # No `provider` block and no credential. Two findings from testing 1.18.7
        # against a live server:
        #   - a per-workspace provider definition is ignored by `opencode serve`;
        #     only the global config is read, so the provider is registered by
        #     the opencode container's entrypoint instead.
        #   - `{env:VAR}` is not interpolated in serve mode, so an apiKey here
        #     would reach the provider verbatim and fail with 401.
        # The key is installed through the auth API at container start, which
        # also keeps it off this shared volume entirely.
        "model": f"{_PROVIDER_ID}/{_MODEL_ID}",
        "mcp": {
            _MCP_SERVER_KEY: {
                "type": "remote",
                # Header-only on purpose. The bridge also accepts the token as a
                # path segment, but a token in the URL is written to the backend
                # access log in plaintext, so that route stays a fallback for
                # clients that provably drop headers rather than the default.
                "url": _env("LABS_MCP_URL", _DEFAULT_MCP_URL),
                "enabled": True,
                "headers": {"Authorization": f"Bearer {mcp_token}"},
                # OpenCode probes a remote MCP server before applying `headers`.
                # Our 401 advertises `WWW-Authenticate`, which it reads as "this
                # server speaks OAuth"; it then fetches
                # /.well-known/oauth-protected-resource, gets a 404, and parks the
                # server in `needs_auth` -- at which point the later, correctly
                # authenticated handshake still succeeds but none of the tools are
                # ever exposed to the model. `false` disables that auto-detection
                # so the static bearer header is used directly.
                "oauth": False,
            }
        },
        "permission": permission,
        "agent": {
            _AGENT_NAME: {
                "mode": "primary",
                "description": (
                    "Nexus project knowledge assistant, grounded in authorized indexed "
                    "documentation and current operational evidence; may manage "
                    "documentation workflows and deliver files into output/."
                ),
                "tools": agent_tools,
                "permission": nexus_permission,
                "maxSteps": 24,
                # The role, and the field this agent shipped without. Absent, a
                # primary agent runs on OpenCode's built-in generic prompt and
                # every Nexus instruction arrives as `AGENTS.md` project
                # context -- material to take into account rather than what the
                # model is. See `nexus_system_prompt`.
                "prompt": nexus_system_prompt(mode),
            },
            _PLAN_AGENT_NAME: _plan_agent(),
            _VERIFY_AGENT_NAME: _verify_agent(),
            **_sealed_builtin_subagents(),
        },
    }


def _output_write_patterns(workspace: Path) -> tuple[str, ...]:
    """Every spelling of `<workspace>/output/*` OpenCode might match against.

    The write tool asks for permission using `path.relative(worktree, target)`,
    and `worktree` is NOT the session's directory. On a server with no git
    project it resolves to `/` -- `GET /path?directory=<workspace>` reports
    `worktree: "/"` alongside the correct `directory` -- so a target inside the
    workspace arrives at the matcher as `labs-workspaces/conversation-42/
    output/x.md`. A plain `output/*` pattern matched none of it, and the first
    live probe of this policy refused a write the policy was written to allow.

    Rather than encode which ancestor OpenCode happens to pick, this emits the
    path once per ancestor, so exactly one of them is correct whatever the
    server resolved and the rest are unmatchable strings.

    Every pattern is anchored on this conversation's own directory name. The
    tempting single pattern -- `*/output/*` -- would also match
    `../conversation-99/output/x.md` and let one conversation write into
    another's workspace; the workspace root is listable, so that is a path the
    model could actually construct. The wildcard here is only ever the filename.
    """

    resolved = workspace.resolve()
    output = resolved / _OUTPUT_DIR
    patterns: list[str] = [f"{output.as_posix()}/*"]
    # `resolved` itself first: that is the worktree OpenCode is documented to
    # use, and the only one that yields the short, readable `output/*`.
    for ancestor in (resolved, *resolved.parents):
        patterns.append(f"{output.relative_to(ancestor).as_posix()}/*")
    return tuple(patterns)


def _edit_permission(workspace: Path) -> dict[str, str]:
    """Deny writes everywhere except the workspace's `output/` directory.

    `edit` is the key that governs this, not `write`. OpenCode has no `write`
    permission at all: the `write`, `edit` and `patch` tools all ask under
    `edit`, and the agent decoder folds a `tools` entry for any of the three
    onto that one key. The `"write": "deny"` this config used to carry therefore
    denied nothing -- an unrecognised key matched against nothing -- and what
    actually stopped the agent writing was `edit` alone.

    Pattern order is the policy: OpenCode evaluates the LAST matching rule, so
    the catch-all comes first and the exceptions after it. Reversing the two
    would allow every write in the workspace.

    If none of the patterns matches -- a future release resolving the worktree
    somewhere new -- every write is denied. That is the right direction to fail
    in: the lane loses file delivery, never its containment.
    """

    permission: dict[str, str] = {"*": "deny"}
    for pattern in _output_write_patterns(workspace):
        permission[pattern] = "allow"
    return permission


def _read_only_tool_map() -> dict[str, bool]:
    """The tool map for an agent that may look at anything and change nothing.

    Every `nexus_*` tool is denied, the read-shaped name prefixes are re-admitted,
    and the capabilities that are not read-only are denied again by exact name --
    so the deny survives even if wildcard permission keys stop being honoured.
    Spawning is denied last and unconditionally: this map is used by every
    subagent in the workspace, and that single line is what makes "a subagent
    cannot spawn a subagent" a property of the configuration rather than a
    sentence in a prompt.

    Extracted because the plan agent, the verifier and the sealed built-ins all
    need exactly this, and three copies would drift the first time a prefix was
    added to one of them.
    """

    tools: dict[str, bool] = {f"{_MCP_SERVER_KEY}_*": False}
    tools.update({f"{_MCP_SERVER_KEY}_{prefix}_*": True for prefix in _PLAN_READ_PREFIXES})
    tools.update({name: False for name in _CONSEQUENTIAL_TOOLS})
    tools.update({tool: True for tool in _ALLOWED_TOOLS})
    tools.update({tool: False for tool in _DENIED_TOOLS})
    tools[_SPAWN_TOOL] = False
    return tools


def _verify_agent() -> dict[str, Any]:
    """A second pass that checks the answer against the evidence, and nothing else.

    A single flat loop has no verification stage: the model that wrote the claim
    is the only thing that ever reads it back, and self-policing is exactly what
    a model is worst at. The remedy is a separate pass with a narrow brief and no
    stake in the answer being right.

    Three properties keep it from becoming a cost or a recursion problem, in
    increasing order of how much they can be argued with:

    * The brief, which is checking, not researching.
    * `maxSteps`, low enough that a verifier which started investigating instead
      runs out before it can redo the turn.
    * The tool map, which does not depend on the model cooperating: read-only,
      every consequential capability denied by exact name, and `task` denied so
      this agent cannot spawn another. That last one is the recursion bound.

    It reports; it does not rewrite. The primary agent owns the answer, so a
    verifier that returned corrected prose would be a second author whose text no
    grounding rule ever saw.
    """

    permission = _base_permission()
    permission["edit"] = "deny"

    return {
        "mode": "subagent",
        "description": (
            "Checks a drafted answer against the evidence gathered this turn: "
            "which claims are supported, which are not, and which counts or "
            "lists were asserted as complete without being read whole. Reports "
            "findings; changes nothing."
        ),
        "tools": _read_only_tool_map(),
        "permission": permission,
        "maxSteps": 8,
        "prompt": (
            "You are checking someone else's draft answer. You are not writing "
            "it, improving it, or answering the question yourself.\n\n"
            "You will be given the question, the draft answer, and what was "
            "looked up to produce it. For each material claim in the draft -- "
            "every count, every name, every list presented as complete, every "
            "stated condition or filter -- decide which of these it is:\n"
            "1. SUPPORTED: the evidence says this, and you can quote the part "
            "that does.\n"
            "2. UNSUPPORTED: no evidence you can see says it. Being plausible is "
            "not being supported.\n"
            "3. OVERSTATED: the evidence partly says it, but the draft is more "
            "certain, more complete or more specific than the evidence allows. "
            "A count taken from ranked search results rather than from a whole "
            "document belongs here.\n"
            "4. CONTRADICTED: two pieces of evidence disagree, and the draft "
            "silently picked one. Name both.\n\n"
            "You may re-read a document or re-run a lookup to settle a specific "
            "claim. Do not start a fresh investigation, and do not go looking for "
            "things the draft did not claim.\n\n"
            "Return a short list: the claim, the verdict, and one line of "
            "justification. If everything checks out, say so in one sentence. "
            "Do not rewrite the answer."
        ),
    }


def _sealed_builtin_subagents() -> dict[str, dict[str, Any]]:
    """Re-declare OpenCode's own subagents as read-only and unable to spawn.

    These exist in the runtime whether this config mentions them or not, and an
    agent key matching a built-in overrides that built-in's fields -- the same
    mechanism `_plan_agent` relies on.

    They are declared because `nexus` now holds a `task` permission. That
    permission is pattern-scoped to the verifier, but the scoping is a claim
    about how OpenCode matches a `task` rule, and a containment boundary should
    not rest on one. If the pattern is honoured these definitions are unreachable
    and cost nothing; if it is not, a spawned `general` or `explore` is still
    read-only, still cannot write, and still cannot spawn.
    """

    permission = _base_permission()
    permission["edit"] = "deny"
    return {
        name: {
            "mode": "subagent",
            "description": (
                "Read-only helper. Cannot change anything and cannot start "
                "another agent."
            ),
            "tools": _read_only_tool_map(),
            "permission": permission,
            "maxSteps": 6,
        }
        for name in _BUILTIN_SUBAGENTS
    }


def _plan_agent() -> dict[str, Any]:
    """A read-only companion agent that says what it would do and does not do it.

    Selected per turn with `agent="plan"`. `plan` is also the name of an
    OpenCode built-in, and using it is deliberate rather than a collision: an
    agent key that matches a built-in overrides that built-in's fields, so this
    definition replaces the stock plan agent instead of competing with a second
    one the user could reach.

    Three things keep it honest, in increasing order of how much they can be
    argued with:

    * The prompt, which says to produce a numbered plan and not to act.
    * `maxSteps`, low enough that an agent which started executing anyway runs
      out of iterations long before it finishes.
    * The tool map, which is the part that does not depend on the model
      cooperating. Every `nexus_*` tool is denied, the read-shaped name
      prefixes are re-admitted, and the capabilities that are not read-only are
      denied again by exact name last -- so the deny survives even if wildcard
      permission keys stop being honoured.
    """

    tools = _read_only_tool_map()

    permission = _base_permission()
    permission["edit"] = "deny"

    return {
        "mode": "primary",
        "description": (
            "Plans an approach without carrying it out: returns a numbered plan of "
            "the steps it would take, the evidence each one would rest on, and what "
            "it cannot answer. Reads only; changes nothing."
        ),
        "tools": tools,
        "permission": permission,
        # Deliberately small. Planning needs enough steps to confirm what
        # exists -- a project lookup and a documentation probe -- and not
        # enough to carry the work out under the guise of
        # investigating it.
        "maxSteps": 6,
        "prompt": (
            "You are planning, not doing.\n\n"
            "Produce a numbered plan of the steps you WOULD take to answer the "
            "user's request, and stop there. Do not carry any step out, do not "
            "write the answer the plan describes, and do not create, save, "
            "generate, onboard or cancel anything -- those are unavailable to you "
            "and attempting them wastes the turn.\n\n"
            "You may make a small number of read-only checks first, but only to "
            "establish what actually exists: whether the project has published "
            "documentation and whether a runtime source is "
            "connected. Do not use them to start answering.\n\n"
            "Return, in this order:\n"
            "1. What you understand the user to be asking for, in one sentence.\n"
            "2. The numbered steps, each naming the evidence it would rest on in "
            "plain terms -- 'the published architecture documentation', "
            "'recent execution records' -- never a tool name.\n"
            "3. What you would NOT be able to determine, and why.\n"
            "4. Anything you need the user to decide or confirm before the work "
            "starts.\n\n"
            "Keep it under 250 words. A plan the reader skims is worthless."
        ),
    }


def _material_roots() -> tuple[str, ...]:
    """The directories under `.nexus/project/` that mounted material owns.

    Derived by asking `workspace_relpath` where each kind lands rather than
    restating "references" and "skills" here. That function is the single owner
    of the layout, and a second, hardcoded idea of it is precisely how a renamed
    directory turns into a tree of files nobody ever cleans up again.

    The probe entries exist only to be passed to `workspace_relpath`, which reads
    `kind` and the slug; `role` is filled with anything valid because it does not
    reach the path.
    """

    roots: set[str] = set()
    for kind in MaterialKind:
        relative = workspace_relpath(
            MaterialEntry(document_id=0, filename="probe", role="reference", kind=kind)
        )
        head, separator, _ = relative.partition("/")
        if not separator:
            # A kind that lands directly in `project/` would put the sweep below
            # in charge of deleting files this function does not own -- RULES.md
            # among them. Fail at import rather than discover that at runtime.
            raise RuntimeError(
                f"workspace_relpath maps material kind {kind.value} to {relative!r}, "
                "at the root of the project directory; the workspace sweep cannot "
                "own that path"
            )
        roots.add(head)
    return tuple(sorted(roots))


#: Computed once, at import, for the same reason the mapping in `material.py`
#: asserts at import: a layout change should break the build, not a turn.
_MATERIAL_ROOTS: Final[tuple[str, ...]] = _material_roots()

# The generation memory owns its own directory under `project/` rather than
# sharing `references/` with the uploaded documents, and the sweep below owns
# both. A collision would put two writers in one swept root: whichever ran
# second would delete the other's files as "not projected". Checked at import
# for the same reason `_material_roots` raises there -- a layout change should
# break the build, not a turn.
if MEMORY_DIRNAME in _MATERIAL_ROOTS:  # pragma: no cover - import-time guard
    raise RuntimeError(
        f"generation memory directory {MEMORY_DIRNAME!r} collides with a material "
        f"root {_MATERIAL_ROOTS!r}; the workspace sweep cannot own both"
    )

#: Workspace-relative paths the projection may stop emitting from one turn to
#: the next, and which therefore have to be removed when it does.
#:
#: `AGENTS.md` and the thread README are absent because the projection always
#: emits them; `opencode.json` is absent because the projection never does and
#: this list is the sweep's licence to delete.
_SWEPT_FILES: Final[tuple[str, ...]] = (
    *(f"{_NEXUS_DIR}/{relative}" for _, relative, _ in _LAYER_FILES),
    f"{_NEXUS_DIR}/{_PROJECT_DIR}/{_MANIFEST_FILE}",
)

#: Workspace-relative directories whose entire contents the projection owns.
#:
#: The generation memory is in here for the reason its own module gives: an
#: extract of a record that has since been emptied, deleted or grown past the
#: ceiling stops being projected, and a stale extract the agent reads with full
#: confidence is worse than no extract at all. Deletion, emptying and growth
#: all present here identically -- as a path that stopped being projected -- so
#: none of them needs a special case.
_SWEPT_ROOTS: Final[tuple[str, ...]] = tuple(
    f"{_NEXUS_DIR}/{_PROJECT_DIR}/{name}"
    for name in (*_MATERIAL_ROOTS, MEMORY_DIRNAME)
)


# -- the system role ---------------------------------------------------------


#: The `nexus` agent's system prompt.
#:
#: It did not have one. OpenCode's agent config supports a `prompt` field -- the
#: `plan` agent below has used it since it was written -- and `nexus` left it
#: unset, so the lane's primary agent ran on OpenCode's built-in generic prompt
#: ("You are opencode, an interactive CLI tool that helps users with software
#: engineering tasks...") and received every Nexus instruction as `AGENTS.md`
#: project context. That difference is not cosmetic. A system prompt is the
#: model's role; `AGENTS.md` is material the role is told to take into account,
#: and a model resolving a conflict between "you are a CLI coding tool" and "a
#: file says to write for a consultant" has been given no reason to prefer the
#: file.
#:
#: What belongs here versus in `AGENTS.md` is decided by one question: does it
#: change between turns?
#:
#:   * HERE: who the agent is, how it chooses a tool, what it may assert, how it
#:     writes. None of it depends on the project, the user or the conversation,
#:     so it is the same bytes on every call and belongs in the role.
#:   * `AGENTS.md`: which project is bound, whether published docs are mounted,
#:     the resolved guidance stack, the manifest, which files exist to read.
#:     All of it varies per turn or per project.
#:
#: Nothing is in both. A duplicated instruction is paid for twice on every call
#: and drifts the day one copy is edited, which is the failure the guidance
#: renderer already documents at length.
#: The mode-invariant head: who the agent is, how it spends a turn, and which
#: source answers which kind of question. Ends on the last of the source
#: bullets, because that is where the scan is inserted in deep-dive mode.
_PROMPT_HEAD: Final = """You are Nexus, an evidence-grounded project knowledge assistant. You help \
consultants and clients understand everything supported by their authorized \
project documentation and operational evidence: business processes, systems, \
data, architecture, controls, operations, integrations, and implementation.

Your knowledge of any estate comes entirely from the tools and files you are \
given in this conversation. You have no independent knowledge of the client, \
and nothing you remember from training is evidence about them.

# Work efficiently

Use the smallest evidence path that can answer the question. A normal question \
does not need a plan merely because it takes two or three reads. Use `todowrite` \
only when the user asks for a plan, the task has at least three independent \
deliverables, or work will continue as a long-running workflow. Keep such a plan \
to two to five outcome-level steps and update it only at meaningful milestones.

Do not write a plan, or any running commentary on it, into your reply. Not \
"I'll start by checking the documentation", not "I see the Mediq branch but not yet \
where it splits", not "that returned nothing, trying another interface document \
instead". Your reply is the ANSWER, and it starts at the answer. Everything \
about how you got there is implementation detail; saying it again buries the \
answer under a transcript of the work.

# Evidence sources

The tool descriptions own the detailed selection rules. Apply these stable \
boundaries:

- Indexed documentation is the default source for documented facts. For an \
  ordinary documentation question, make one `search_documentation` call first. \
  Its returned passages are answer-ready evidence: answer from them immediately \
  when they support the requested explanation.
- `search_within_document` returns ranked passages from one known document. It \
  never establishes that a list, mapping, procedure, or count is complete.
- `read_document` reads an outline, one section, or several sections in document \
  order, each complete. \
  It is a precision fallback, not a required second stage after search. \
  Use it only for a specific detail that is missing or truncated, \
  or when the user needs a complete ordered list, mapping, procedure, or count. \
  Do not open an outline or section merely to gather more context, \
  and use full-document mode only for a small document. \
  When you need more than one section, name them all in a single `sections` call. \
  Reading them one per call costs the same evidence several times over, \
  because every call re-sends the conversation behind it.
- Generated Markdown artifacts are workflow outputs, not the indexed corpus. \
  `list_generated_documents` and `read_generated_document` inspect those outputs; \
  `start_documentation_generation` starts new work and \
  `get_documentation_generation_status` only reports its progress.
- Runtime tools answer present-tense questions about executions, failures, health, \
  synchronization, and logs. Do not use them for documented design questions.
- Pack tools manage reusable documentation instructions. A Pack is configuration, \
  not evidence about a project.
"""

#: Deep dive only: the raw scan joins the source list, and the economy rules are
#: restated against it.
#:
#: The restatement is not padding. "More sources" reads to a model as permission
#: to gather, and the measured cost of gathering on this lane was a turn that
#: spent 34 tool calls before it answered anything. The mode widens the menu;
#: the per-turn budget above is unchanged, and this says so at the point the
#: wider menu is introduced rather than leaving it to be inferred from a section
#: the model read several thousand tokens earlier.
_PROMPT_SCAN_SOURCE: Final = """- `search_source` and `read_component` read the SCANNED SOURCE of the project \
rather than the documentation written from it. Deep dive is on for this turn, so \
both are available to you. They carry what a summary drops: component \
parameters, filter and mapping expressions, conditions, routing rules, and the \
literal SQL a job runs. `search_source` finds where a term, column, or \
identifier actually appears; `read_component` returns one named component's \
configured parameters in full.
- The scanned source is the authority when it and the documentation disagree. \
Documentation is written from a scan and can be stale, partial, or summarised; \
the scan is the configuration itself. When the two differ, give the scanned \
value, say the documentation records something else, and never settle it by \
picking the more convenient reading.

Deep dive widens the sources, not the budget. Everything under "Work \
efficiently" still applies. Start on the documentation path, reach for the scan \
for the specific parameter, expression, condition, or query the documentation \
does not carry, and stop there. One source search and one component read on top \
of the ordinary path is the normal shape of a deep-dive answer. Do not sweep \
the scan to see what is in it, and do not read in source what you have already \
read in documentation.
"""

#: The mode-invariant middle: completeness discipline, the per-turn call budget,
#: and the head of the absence rules. Ends on the "Required instead" bullet,
#: because what the next step IS depends on which sources this turn can reach.
_PROMPT_EVIDENCE_DISCIPLINE: Final = """For an exact list, count, mapping, procedure, rule, filter, or expression, read \
the complete relevant section. Never reconstruct completeness from ranked \
fragments. Never invent or infer a missing configured expression.

Stop as soon as the answer is adequately supported. Ordinary documentation \
questions should normally take one to three evidence calls, starting with one \
search. Exact mappings and other completeness requests should normally take \
three to five. Exceed those targets only when a concrete unanswered part of the \
user's request requires it. Never respond to an oversized full-document result \
by firing one call per section. Choose the smallest section set that answers the \
question, ask for all of it in a single `sections` call, and if that is still \
too much, give the best bounded answer and state the gap.

Match the investigation to the question. Do not expand a design or documentation \
answer into monitoring, security, runtime behavior, operational risks, or adjacent \
interfaces unless the user asks for them or they are essential to answer safely \
and correctly. More available evidence is not a reason to retrieve it.

# What you may say about what you did not find

You may never state that something does not exist in the client's estate. You \
have not searched their estate; you have used a few tools over an indexed \
subset of it, and every tool tells you what it covered in the `scope` of its \
result.

- Forbidden: "there is no filter", "this is not documented", "no such job \
exists", "nothing records that".
- Required instead: "I did not find X in <what you actually searched>", \
followed by the next step that would settle it.
"""

#: Chat mode's half of the absence rules, and the higher-value half of this
#: whole change.
#:
#: The measured failure it answers: asked which orders go through a particular
#: partner, the agent searched the generated documentation, found no filter, and
#: published "I did not find any filter ... confirm with the functional owner".
#: The scan answers the question outright -- the condition is a literal
#: `ABCLASS01 = 'FMD'` in one component's parameters -- and none of those terms
#: appears anywhere in the documentation, because summarising them away is what
#: documentation IS. The old text made that reply compliant: it did not assert
#: non-existence, it named what it searched, and it offered a next step. The
#: next step was just the wrong one, and it moved the work to the client.
#:
#: So the rule is not "never say you did not find it". It is "an absence in the
#: documentation is an absence in the summary", and the next step that settles
#: it is a mode this product already has.
_PROMPT_ABSENCE_CHAT: Final = """The indexed documentation is a SUMMARY of a scan of the client's estate, and a \
summary drops exactly the things people ask about: component parameters, filter \
expressions, join conditions, and the SQL a job actually runs. The scanned \
source behind it is not in this turn's tool menu. So "the documentation does \
not record it" is a statement about the summary, and never about the system.

When the question asks for a configured detail -- which records are selected, \
what a filter tests, how a field is mapped, what a query returns -- and the \
documentation does not carry it, do not close on the absence and refer the user \
to the functional owner. Say what the documentation does record, say that it \
does not record that detail, say that the scanned source behind it may well \
carry it, and offer to continue in Deep Dive. That is the next step that would \
settle it, and offering it is your job rather than theirs.
"""

#: Deep dive's half of the same rules. The escalation the chat text offers is
#: already spent, so absence has to be earned against the source before it may
#: be reported at all.
_PROMPT_ABSENCE_DEEP_DIVE: Final = """Deep dive is on, so the scanned source is already available to you. Before \
reporting that you did not find a configured detail -- a filter, a condition, a \
mapping expression, a query -- search the source for it. Reporting an absence \
with the source tools untouched is reporting on a search you chose not to run.

When the source does not carry it either, say so precisely: name what you \
searched in the documentation AND in the source, and say that the detail is not \
configured where you looked. That is still not the same claim as "it does not \
exist", and you still may not make that one.
"""

#: The mode-invariant tail: what an empty result means, verification, the hard
#: rules, and how to write the answer.
_PROMPT_TAIL: Final = """When a call returns nothing, read the `scope` it came back with and say what \
was covered -- the project, how many documents, which roles, which time \
window. Then either take the next step yourself or name it for the user. An \
empty result is a fact about your search. It is never a fact about the client.

The same rule applies to completeness. Do not say "the 12 fields are..." unless \
you read something that could tell you there are twelve. If you counted from \
ranked passages, say that you may have missed some and offer to read the \
document whole.

# Check the answer before you give it

Check material claims against the evidence already read. A count or complete \
list from a complete section needs no second agent. Use a verifier only when \
the user explicitly asks for an audit, the answer is high-stakes, or two \
authoritative sources conflict. Give the verifier the actual evidence already \
collected and do not let it repeat the investigation.

- Unsupported claims are removed or plainly marked unconfirmed.
- A count from ranked search results is "at least N visible in these results", \
  never an exact total.
- Contradicting authoritative sources are both named; never silently pick one.

# Hard rules

- A skill offered to you is a procedure already worked out. When one matches \
the request, load it and follow it rather than improvising.
- `output/` is the only place you can write. Put a file there when the user \
asks for something to keep -- a diagram, a report, a table -- and tell them \
what you called it. Nothing else is writable and you cannot run commands; \
anything that changes a project happens through a `nexus_*` tool that says so.
- Answer in the chat. Write a file ONLY when the user explicitly asks for one. \
A diagram, a table or a summary they asked to SEE belongs in the reply. If you \
do write a file, name it in one short line and still show the content in chat.

# Write for the reader, not about your plumbing

The reader is a consultant or a client, not the engineer who built you. They \
cannot see your tools and do not know what any of them are called.

Never put these in an answer:

- Tool names, capability ids, operation names, or the fact that you called \
anything.
- Running commentary on your own steps -- "now checking the collector", "that \
came back empty, trying the interface document". The plan card already shows \
the reader which step you are on.
- Retries, timeouts or fallbacks -- "that call timed out, retrying", \
"switching to the draft workflow". Just do it, and report the outcome.
- Internal identifiers: draft numbers, run ids, claim ids, session ids, \
evidence refs.
- Absolute or temporary paths. Name a document by its title, a deliverable by \
its name in `output/`.
- Your own limits, stages, or configuration.

If work is still running, say so in plain terms -- "building the pack, this \
takes a minute". If it failed, say what the user cannot now do and what would \
fix it. If you changed approach, the reader needs only the result.

A good answer reads as though a knowledgeable colleague wrote it after doing \
the work, not as a log of the work being done. Attribute facts in prose where \
it matters -- "per the executive summary", "the interface document reports" -- and \
say plainly where you could not verify something.

# Name things precisely

Your reader is using real project knowledge to make a decision. Give the \
concrete business terms, systems, documents, owners, jobs, components, queues, \
tables, fields, controls, or deployment states that matter to the question. Do \
not force implementation detail into a business question, and do not replace a \
technical identifier with a vague paraphrase when the identifier is the answer.

This does not contradict the section above. Keep tool CALLS and internal ids \
out of the prose; put the DOMAIN identifiers those tools returned in. When you \
describe a relationship whose sequence is genuinely hard to follow in prose, a \
small fenced ```mermaid `flowchart` may be clearer; use one only when it helps.

# Length

Let the question set the length. A lookup gets a sentence; tracing a chain, an \
inventory or a deep dive gets whatever it takes to name every part. Never drop \
a concrete name to save words -- padding is the thing to cut, detail is not.
"""


def nexus_system_prompt(mode: LabsTurnMode = LabsTurnMode.CHAT) -> str:
    """The `nexus` agent's system role for one turn, in the given evidence mode.

    Assembled from chunks rather than stored twice. The two modes share the role,
    the economy rules, the completeness discipline, the writing rules and the
    hard rules verbatim -- they differ in exactly two places, and holding two
    full copies would mean every future edit to the shared 95 percent has to be
    made twice or silently drifts. That is the same argument the module already
    makes for keeping `AGENTS.md` and this prompt disjoint.

    Joined on a blank line: every chunk ends with a newline and none starts with
    one, so `"\\n".join` reproduces the paragraph spacing the single-string
    version had, and a chunk omitted for a mode leaves no gap behind.

    Default is `CHAT`. A caller that forgets to state a mode gets the narrow
    prompt, which matches the tool menu a caller that forgets gets from
    `src/labs/mcp_bridge.py`.
    """

    absence = (
        _PROMPT_ABSENCE_DEEP_DIVE
        if mode is LabsTurnMode.DEEP_DIVE
        else _PROMPT_ABSENCE_CHAT
    )
    sources = _PROMPT_HEAD
    if mode is LabsTurnMode.DEEP_DIVE:
        # Concatenated with no blank line between them, unlike every other join
        # here: the scan entries are more bullets in the source list that the
        # head ends on, and a blank line would start a second list under the
        # same heading -- which reads as a second, separate class of source.
        sources += _PROMPT_SCAN_SOURCE
    return "\n".join([sources, _PROMPT_EVIDENCE_DISCIPLINE, absence, _PROMPT_TAIL])


#: The chat-mode role, kept as a module constant because it is the default and
#: because it is what every caller that has no turn in hand -- the workspace
#: projection the user reads, the tests that pin the wording -- should see.
#: Deep dive is reached through `nexus_system_prompt`, never through a second
#: constant.
_NEXUS_SYSTEM_PROMPT: Final = nexus_system_prompt(LabsTurnMode.CHAT)

#: Where the system prompt is written inside the workspace, for the user's sake.
#:
#: The prompt reaches the model through `opencode.json`, which is deliberately
#: never projected because it carries the MCP bearer. Without this file the
#: read-only workspace API -- the one surface where a user can see what their
#: assistant was actually told -- would show them everything except the part
#: with the most authority over the answer. So the text is projected as a file
#: as well, exactly as each guidance level is projected alongside being inlined
#: into `AGENTS.md`.
_SYSTEM_PROMPT_FILE: Final = f"{_NEXUS_DIR}/SYSTEM_PROMPT.md"

#: Origin marker for that file. Distinct from `ORIGIN_ASSEMBLED` because the two
#: reach the model by different routes -- one is the system role, the other is
#: project context -- and a UI that flattens them would be describing a
#: distinction the lane spent this change creating.
ORIGIN_SYSTEM_PROMPT: Final = "system_prompt"


def _material_document(entry: MaterialEntry, text: str) -> str:
    """One mounted document: a provenance header, then the stored text.

    The header answers a question the agent cannot otherwise answer about any
    file inside the boundary -- is this something a person wrote here, or a copy
    of something that lives elsewhere? It matters for what the agent may say: a
    mounted extract can be quoted and attributed to the uploaded document, while
    treating it as workspace content would have the agent describe a file path
    the reader has never heard of.

    The filename is whitespace-collapsed because it is interpolated into a
    Markdown blockquote; a newline in it would end the quote early and leave the
    remainder of the header reading as the document's own first line.
    """

    filename = " ".join(entry.filename.split())
    return (
        f"> Extracted copy of uploaded document {entry.document_id} "
        f"(role: {entry.role}, file: {filename}).\n"
        "> Nexus wrote this file from the stored document text. It is not authored "
        "content and nothing written here is read back.\n"
        "\n"
        f"{text.rstrip()}\n"
    )


def _memory_document(document_body: str) -> str:
    """One mounted memory extract.

    A pass-through today, and named anyway: `_material_document` is the shape
    this mirrors, and the provenance header for a memory extract is built in
    :mod:`src.labs.generation_memory` because it has to state entry counts and
    what was omitted -- facts this module does not hold. Having the seam here
    is what keeps a later change from putting a second header on the file.
    """

    return document_body


def _render_agents_md(
    *,
    project_id: str | None,
    docs_available: bool,
    guidance: ResolvedGuidance,
    nexus_layers: tuple[GuidanceLayer, ...],
    material: ProjectMaterial | None,
    manifest_available: bool,
    memory: GenerationMemory | None,
) -> str:
    """Assemble `AGENTS.md`: the guidance stack, then what varies per turn.

    This file is the VARIABLE half of the context. The invariant half -- who the
    agent is, how it picks a tool, what it may assert, how it writes -- moved to
    `_NEXUS_SYSTEM_PROMPT`, which reaches the model as its system role rather
    than as a file it is asked to take into account. Nothing appears in both:
    every duplicated line is paid for twice on every call and drifts the day one
    copy is edited.

    What survives here is what a system prompt could not carry, because it
    differs between turns, projects or users: the resolved guidance stack, which
    project is bound, whether published documentation is mounted and how to
    search it, and which files actually exist to read.

    The order is a caching decision, not a layout preference. `render_guidance`
    emits organization -> project -> person, and everything above the person
    section is byte-identical across every turn and every user on the same
    project, so the assembled head of this file is a stable prefix a provider
    cache can keep hold of. Everything that varies per conversation -- the project
    binding line, whether docs are mounted, the pointer section -- is therefore
    placed below it. Putting any of it on top would invalidate the whole prefix on
    every turn and buy nothing.

    Procedure is absent for a third reason again: how to review an estate, build
    a Pack, investigate a runtime failure or onboard a project lives in a Skill,
    which costs its name and description until the model selects it and its body
    only afterwards.

    `nexus_layers` is what the projection actually emits a file for, not what the
    levels could contain. The closing pointer section names only files that exist, for
    the same reason `docs_line` branches on the mount: an instruction to read a
    path that is not there buys a failed `read`, a wasted step, and a model that
    learns to distrust the rest of the section. `manifest_available` is the same
    fact for `.nexus/project/MANIFEST.md`.

    `material` is handed to `render_guidance`, which places the manifest inside
    the guidance block. Rendering it a second time here would put two copies of
    the same list in the always-on prefix.

    `memory` renders BELOW the guidance block rather than inside it, and that
    placement is the same caching decision as everything else down here: the
    guidance head is byte-identical for every user on a project and is what a
    provider prefix cache holds, while the memory section moves whenever a
    documentation run appends an entry. It is also not guidance -- nothing in
    it is a rule anyone authored for the agent to follow -- so putting it in
    the stack would give a run's working notes the standing of an org or
    project rule.
    """

    project_line = (
        f"You are answering questions about project `{project_id}`."
        if project_id
        else "No single project is bound to this conversation; ask which project is meant "
        "before answering anything project-specific."
    )
    if docs_available:
        docs_line = (
            "`docs/` holds the same published documentation the `nexus_*` tools index. "
            "It is read-only, and it is the FALLBACK: reach for it only after a tool "
            "came back empty or lacked a detail, and say which tool fell short. A file "
            "costs tens of thousands of tokens against a tool call's fraction of that.\n"
            "\n"
            "Work in this order: `list` with path `docs` to see what is there,\n"
            "then `grep` with path `docs` to find the file,\n"
            "then `read` on `docs/<filename>` to open it.\n"
            "\n"
            "**`docs/` is a mount, and every search tool needs to be pointed at it by "
            "name.** `grep` and `glob` walk the workspace without following the mount, "
            "so a search that does not pass `docs` as its path returns NOTHING from the "
            "documentation. An empty result from such a call means the call was wrong; "
            "it is never evidence that the documentation is silent. Always pass `docs` "
            "(or a path under it) explicitly, and if a documentation search comes back "
            "empty, re-run it with the path set before you conclude anything from it."
        )
    else:
        docs_line = (
            "There is NO `docs/` directory here: the project has no published "
            "documentation, or none is bound to this conversation. Never claim to have "
            "read a documentation file; use the `nexus_*` tools, or say plainly that the "
            "documentation is unavailable."
        )

    # Ordered most-specific first: the two levels that answer a question the
    # standing instructions cannot, then the organization floor, then the mount.
    pointers: list[str] = []
    if GuidanceLayer.PROJECT in nexus_layers:
        pointers.append(
            "- `.nexus/project/RULES.md` -- read before answering anything that uses a "
            "client term new to you."
        )
    if manifest_available:
        pointers.append(
            "- `.nexus/project/MANIFEST.md` -- the project's uploaded material, the same "
            "list as above; the files it names sit beside it under `.nexus/project/`."
        )
    if GuidanceLayer.PERSON in nexus_layers:
        pointers.append(
            "- `.nexus/user/PREFERENCES.md` -- read before writing a long or a "
            "structured answer."
        )
    if GuidanceLayer.ORG in nexus_layers:
        pointers.append(
            "- `.nexus/org/RULES.md` -- the organization rules at the top of this file, "
            "verbatim."
        )
    if docs_available:
        pointers.append(
            "- `docs/` -- published documentation; the fallback described above."
        )
    pointer_lines = "\n".join(pointers)

    # Only sections with something to say. An empty heading tells the model the
    # project has no memory worth honouring, which is a stronger and less true
    # statement than saying nothing -- the renderer makes the same argument
    # about an empty guidance level.
    memory_section = render_memory_section(memory)
    memory_block = f"\n{memory_section}\n" if memory_section else ""

    return f"""{render_guidance(guidance, material=material)}

# This conversation

{project_line}

Your role, how to choose a tool, what you may assert and how to write are in your
system instructions and are not repeated here. What follows is only what is true
of THIS conversation.

## Published documentation on disk

{docs_line}
{memory_block}
## Where to look when you need more

{pointer_lines}
"""


# -- the layout, as data -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class WorkspaceFile:
    """One file the workspace contains, described without a filesystem.

    `path` is workspace-relative and always POSIX-separated: it is a contract
    string, shown to a user and matched against by the sweep, not a local path.

    `always_loaded` is the field that carries the product meaning. True means the
    bytes are in front of the model on every single turn -- they are inside
    `AGENTS.md`, which OpenCode loads unprompted -- and therefore cost tokens
    whether or not they are relevant to the question. False means the agent
    reaches the file only if it decides to, which costs nothing until it does.
    That distinction is invisible from a directory listing and is the whole
    reason a user needs this projection rather than a file tree.
    """

    path: str
    content: str
    origin: str
    always_loaded: bool


def project_workspace_files(
    *,
    project_id: str | None,
    guidance: ResolvedGuidance | None,
    material: ProjectMaterial | None,
    material_texts: Mapping[int, str] | None,
    docs_mounted: bool,
    memory: GenerationMemory | None = None,
    mode: LabsTurnMode = LabsTurnMode.CHAT,
) -> tuple[WorkspaceFile, ...]:
    """The complete workspace layout for one turn: every file, and its content.

    `mode` affects exactly one file: the projected copy of the system prompt.
    It has to, because that copy exists to show a user what their assistant was
    actually told, and a deep-dive turn is told something different. It defaults
    to chat so the read-only workspace endpoint -- which has no turn in hand and
    is describing the conversation at rest -- keeps showing the standing role.

    Pure. No `Path`, no environment, no clock, no I/O, and the same inputs always
    produce the same tuple in the same order -- which is what lets a read-only API
    show a user the worktree without creating one, and what lets two callers
    disagree about nothing.

    Ordering is the system prompt, `AGENTS.md`, the guidance levels in stack
    order, the thread note, the manifest, mounted material by path, then the
    generation-memory extracts by path. It is the order a person reads the
    workspace in, and it is also the order of authority: the role first, then
    the project context, then what the context was assembled from, then what
    the agent may go and fetch. The memory comes last of all because it is the
    only tier nobody authored for the agent -- it is one pipeline's working
    notes about the estate, useful precisely because it records doubt, and it
    outranks nothing above it.

    `opencode.json` is deliberately NOT part of this projection, and must never
    be added to it. It carries the MCP bearer token, this tuple is returned
    verbatim to a browser by the workspace endpoint, and a credential that
    reaches a UI is a credential in a screenshot, a support ticket and a browser
    cache. It is written directly by `ensure_labs_workspace`, which is the only
    place that has the token at all.

    `docs_mounted` is passed in rather than probed because probing is I/O. It
    changes `AGENTS.md`: the text tells the agent either where the published
    documentation is or that there is none, and an instruction to read a mount
    that does not exist costs a failed `read` and the credibility of the rest of
    the section.

    `guidance=None` is not "no rules" -- the organization invariants are
    code-owned constants in the renderer, so an empty `ResolvedGuidance` still
    projects `.nexus/org/RULES.md` in full. `material=None` resolves the other
    way and projects nothing: there is no safe default text for a client policy,
    so an agent that sees nothing and says so beats one reading a copy whose
    source may have been withdrawn.

    `memory=None` resolves like `material=None`, and for a stronger version of
    the same reason: the generation memory is an extract of a record this
    process does not own, so "the compile failed" and "the record is gone" have
    to produce the same workspace. Defaulted rather than required only because
    it is additive -- a caller that has not been taught to compile it gets
    exactly the workspace it got before.
    """

    resolved = ResolvedGuidance() if guidance is None else guidance
    texts: Mapping[int, str] = {} if material_texts is None else material_texts

    # A level that renders empty is not projected, so the sweep removes whatever
    # a previous turn left there. The renderer's own rule is silence over empty
    # scaffolding; on disk the stronger form applies, because a stale
    # `project/RULES.md` states rules that no longer resolve. ORG is exempt -- it
    # is the safety floor and is always present.
    layer_files: list[WorkspaceFile] = []
    written_layers: list[GuidanceLayer] = []
    for layer, relative, origin in _LAYER_FILES:
        body = render_layer(resolved, layer)
        if not body and layer is not GuidanceLayer.ORG:
            continue
        layer_files.append(
            WorkspaceFile(
                path=f"{_NEXUS_DIR}/{relative}",
                content=body + "\n",
                origin=origin,
                # Byte-for-byte the same text `AGENTS.md` opens with. The file is
                # a cheap re-read, not a second source, so it carries the same
                # always-loaded truth as the block it copies.
                always_loaded=True,
            )
        )
        written_layers.append(layer)

    manifest = render_manifest(material) if material is not None else ""
    material_files: list[WorkspaceFile] = []
    if material is not None:
        for entry in material.mountable:
            text = texts.get(entry.document_id)
            # A mountable entry whose text did not arrive gets no file and no
            # placeholder: the manifest already tells the agent to search that
            # document, whereas an empty file would tell it the document is empty.
            if not text:
                continue
            material_files.append(
                WorkspaceFile(
                    path=f"{_NEXUS_DIR}/{_PROJECT_DIR}/{workspace_relpath(entry)}",
                    content=_material_document(entry, text),
                    origin=f"{ORIGIN_DOCUMENT_PREFIX}{entry.document_id}",
                    # The manifest *line* naming this file is always loaded; the
                    # file itself is not. That asymmetry is the design -- it is
                    # how a 30k-character policy stays reachable for the price of
                    # one sentence per turn.
                    always_loaded=False,
                )
            )
    material_files.sort(key=lambda file: file.path)

    manifest_files: tuple[WorkspaceFile, ...] = ()
    if manifest:
        manifest_files = (
            WorkspaceFile(
                path=f"{_NEXUS_DIR}/{_PROJECT_DIR}/{_MANIFEST_FILE}",
                content=manifest + "\n",
                origin=ORIGIN_MANIFEST,
                # Same reasoning as the guidance levels: `render_guidance` places
                # this exact text inside `AGENTS.md`, so it is already being paid
                # for on every turn.
                always_loaded=True,
            ),
        )

    memory_files: list[WorkspaceFile] = []
    if memory is not None:
        for document in memory.documents:
            memory_files.append(
                WorkspaceFile(
                    path=f"{_NEXUS_DIR}/{_PROJECT_DIR}/{document.relpath}",
                    content=_memory_document(document.body),
                    origin=f"{ORIGIN_GENERATION_PREFIX}{document.filename}",
                    # The same asymmetry the mounted documents have: the line
                    # naming this file is always loaded, the file is not. It is
                    # how a 30 KB question list stays reachable for the price of
                    # one sentence per turn.
                    always_loaded=False,
                )
            )
    memory_files.sort(key=lambda file: file.path)

    agents_md = WorkspaceFile(
        path=_AGENTS_FILE,
        content=_render_agents_md(
            project_id=project_id,
            docs_available=docs_mounted,
            guidance=resolved,
            nexus_layers=tuple(written_layers),
            material=material,
            manifest_available=bool(manifest),
            memory=memory,
        ),
        origin=ORIGIN_ASSEMBLED,
        always_loaded=True,
    )

    thread_readme = WorkspaceFile(
        path=f"{_NEXUS_DIR}/thread/README.md",
        content=_THREAD_README,
        origin=ORIGIN_THREAD,
        # The one guidance level whose file is not in `AGENTS.md`: it has no
        # content to inline, only a note explaining where the history really is.
        always_loaded=False,
    )

    system_prompt = WorkspaceFile(
        path=_SYSTEM_PROMPT_FILE,
        content=nexus_system_prompt(mode),
        origin=ORIGIN_SYSTEM_PROMPT,
        # True, and the strongest case of it in the projection: this is the
        # system role, so it precedes every message in the conversation rather
        # than merely being attached to each one. It is the one always-loaded
        # file whose bytes are NOT inside `AGENTS.md` -- they reach the model
        # through `opencode.json`, which is never projected because it carries
        # the bearer. Writing it here is what keeps the user's view of their
        # workspace honest.
        always_loaded=True,
    )

    return (
        system_prompt,
        agents_md,
        *layer_files,
        thread_readme,
        *manifest_files,
        *material_files,
        *memory_files,
    )


#: The two origins whose bytes are genuinely paid for once per turn each.
#:
#: Every OTHER always-loaded file is a byte-for-byte copy of a section of
#: `AGENTS.md`, so summing the flag would report roughly twice the real per-turn
#: cost and invite an operator to go trimming a stack half the size they were
#: shown. These two overlap with nothing: the assembled file is the project
#: context, the system prompt is the role, and neither contains the other.
_INDEPENDENT_ALWAYS_LOADED: Final = frozenset({ORIGIN_ASSEMBLED, ORIGIN_SYSTEM_PROMPT})


def always_loaded_tokens(files: Iterable[WorkspaceFile]) -> int:
    """What the always-loaded set costs the model on one turn, in tokens.

    Counted over the assembled file and the system prompt rather than summed
    over everything flagged `always_loaded` -- see
    `_INDEPENDENT_ALWAYS_LOADED` for why the rest would double-count.

    Uses `estimate_tokens`, so this number means what the layer budgets in
    `LAYER_TOKEN_BUDGETS` mean.
    """

    return sum(
        estimate_tokens(file.content)
        for file in files
        if file.always_loaded and file.origin in _INDEPENDENT_ALWAYS_LOADED
    )


# -- writing the projection out ----------------------------------------------


def _mount_docs(workspace: Path, docs_source: Path | None) -> bool:
    """Point `docs` at the project's published documentation, and say whether it did.

    Returns False for every case where the agent must not be told to read the
    mount: no published docs, or a real directory sitting where the symlink
    belongs, which is user data this function will not touch.

    A symlink, and it has to be. The workspace root and the published docs are
    two different mounts in Compose -- `labs_workspaces` (a named volume) and
    `./knowledge_base/output` (a read-only bind) -- so they are on different
    filesystems and a hardlink farm is not an option; measured in the live
    container, `ln` across them fails with `EXDEV: Invalid cross-device link`.
    Copying was rejected separately: it would duplicate client documentation into
    a writable volume and go stale between turns.

    The consequence the agent has to be told about (and is, in `_render_agents_md`):
    every ripgrep-backed tool skips symlinks it meets while WALKING, and only
    descends one that is named as an explicit path argument. Measured in
    `/labs-workspaces/conversation-579`, whose `docs` holds 474 files:

        rg --files --glob='docs/**' .   ->   0 files   (walk: mount skipped)
        rg --files -L --glob='docs/**' .-> 474 files   (--follow, which the tools
                                                        do not pass)
        rg --files docs                 -> 474 files   (explicit path)
        rg -c Talend .                  ->   0 files
        rg -c Talend docs               -> 214 files   (explicit path)

    OpenCode 1.18.7 builds both tools this way: `glob` runs
    `rg --no-config --files --glob=<pattern> .` with cwd resolved from its own
    `path` argument, and `grep` runs `rg --json ... -- <pattern> <path|.>`.
    Neither passes `--follow`, so both are correct only when handed `docs`.
    """

    docs_link = workspace / "docs"
    if docs_source is None:
        if docs_link.is_symlink():
            # Project unbound or its docs were removed: a dangling mount would
            # make the agent cite files that no longer resolve.
            docs_link.unlink()
        return False

    if docs_link.is_symlink():
        if Path(os.readlink(docs_link)) != docs_source:
            docs_link.unlink()
            docs_link.symlink_to(docs_source, target_is_directory=True)
        return True
    if docs_link.exists():
        logger.error(
            "labs workspace %s has a real 'docs' directory; not replacing it", workspace
        )
        return False
    docs_link.symlink_to(docs_source, target_is_directory=True)
    return True


def _write_projection(workspace: Path, files: Sequence[WorkspaceFile]) -> None:
    """Write every projected file, then remove everything the projection dropped.

    Blocking on purpose: the caller runs this in one worker thread so the whole
    directory reaches a consistent state in one hop off the event loop rather
    than in one hop per file.

    The sweep is bounded to paths the projection owns -- the three guidance
    levels, the manifest, the two material directories and the generation
    memory's. `.nexus/project/` also holds `RULES.md`, and the workspace holds a
    symlink out to the client's published documentation; a sweep that reasoned
    about "files I did not write" over a wider root would eventually reach one of
    them.

    Deleting a document, re-roling it, growing it past the size ceiling, or a
    memory file losing its last entry all present identically from here -- as a
    path that stopped being projected -- so one comparison covers all of them and
    none needs a special case.
    """

    projected = frozenset(file.path for file in files)
    for file in files:
        path = workspace / file.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(file.content, encoding="utf-8")

    for relative in _SWEPT_FILES:
        if relative not in projected:
            (workspace / relative).unlink(missing_ok=True)

    for root_relative in _SWEPT_ROOTS:
        root = workspace / root_relative
        if not root.is_dir():
            continue
        # Reverse lexicographic order visits children before parents: a path is
        # always a string prefix of its descendants, so every descendant sorts
        # after it and, reversed, before it. That is what lets one pass unlink
        # the files and then rmdir the skill directory they left empty.
        for path in sorted(root.rglob("*"), reverse=True):
            # Symlinks are checked first and unlinked rather than followed: an
            # `is_file()` test would follow one out of the boundary, and the
            # target is not this function's to reason about.
            if path.is_symlink() or path.is_file():
                if path.relative_to(workspace).as_posix() not in projected:
                    path.unlink()
                continue
            # Only empties succeed, which is exactly the intent: a skill
            # directory whose SKILL.md was just removed goes, one that still
            # holds something stays and is visible.
            with contextlib.suppress(OSError):
                path.rmdir()
        with contextlib.suppress(OSError):
            root.rmdir()


async def labs_docs_mounted(project_id: str | None) -> bool:
    """Published docs are reached through scoped Nexus tools, never a shared mount."""

    del project_id
    return False


async def ensure_labs_workspace(
    *,
    conversation_id: int,
    project_id: str | None,
    mcp_token: str,
    guidance: ResolvedGuidance | None = None,
    material: ProjectMaterial | None = None,
    material_texts: dict[int, str] | None = None,
    memory: GenerationMemory | None = None,
    mode: LabsTurnMode = LabsTurnMode.CHAT,
) -> str:
    """Create or refresh the workspace for one labs conversation.

    `mode` is the turn's evidence breadth, and this function is the only thing
    that makes it expressible in a prompt at all: it runs on every turn and
    rewrites `opencode.json` in place, so the `nexus` agent's system role can
    differ between two turns of the same conversation even though the file
    itself is per conversation. It is written into both copies of the role --
    the one in `opencode.json` that reaches the model, and the projected
    `.nexus/SYSTEM_PROMPT.md` the user can read -- so the two cannot disagree
    about what this turn was told.

    Idempotent: a second call updates `opencode.json` (the MCP bearer may have
    been re-minted), `AGENTS.md` and the `.nexus/` tree in place, re-points the
    removes any legacy `docs` symlink, and brings the mounted material back in
    line with what the project holds now. It never deletes the directory.

    `guidance=None` is not "no rules". The organization invariants are code-owned
    constants in the renderer, so an empty `ResolvedGuidance` still renders
    `.nexus/org/RULES.md` and the head of `AGENTS.md` in full. A guidance lookup
    that failed -- an unreachable database, a half-applied migration, a caller
    that has no session -- may cost this turn its project vocabulary and the
    reader their presentation overlay; it must never cost the safety floor.

    `material=None` resolves the other way, and deliberately: it means no
    material this turn, and last turn's mounted files are removed. Guidance has a
    code-owned floor to fall back on, material does not -- there is no safe
    default text for a client policy -- so the only honest fallback is an agent
    that sees nothing and says so, rather than one reading a copy whose source
    may since have been withdrawn.

    `material_texts` is keyed by document id and only consulted for entries in
    `material.mountable`; an id it does not carry is simply not written.

    `memory=None` behaves exactly as `material=None`: last turn's extracts of
    the documentation run's memory are removed. Compiling it is the caller's
    job (`generation_memory.compile_generation_memory`) for the same reason the
    material compile is -- it is a read this function has no session or scope
    for, and folding it in here would make the writer responsible for deciding
    what the workspace may contain.

    What the directory contains is not decided here. `project_workspace_files`
    owns that, and this function writes what it is handed -- which is what keeps
    the workspace a user is shown and the workspace an agent is given the same
    object rather than two descriptions of one.
    """

    workspace = Path(labs_workspace_path(conversation_id))
    config = _build_opencode_config(
        mcp_token=mcp_token, workspace=workspace, mode=mode
    )

    def _write() -> tuple[bool, tuple[WorkspaceFile, ...]]:
        workspace.mkdir(parents=True, exist_ok=True)

        # The one writable directory, and the only part of the workspace this
        # module creates without owning the contents. It is deliberately absent
        # from `project_workspace_files`: that projection describes files Nexus
        # authored and sweeps anything it stops emitting, and a delivered file
        # the user asked for must survive the next turn. Created unconditionally
        # so the agent never has to reason about whether the path exists.
        (workspace / _OUTPUT_DIR).mkdir(exist_ok=True)

        # Remove the retired shared-output mount. Documentation is available
        # through the project-scoped MCP/RAG tools; a symlink outside this
        # conversation would require broad external-directory read access.
        linked = _mount_docs(workspace, None)

        # Written here and never projected. This is the file that carries the MCP
        # bearer, and `project_workspace_files` documents why it must stay out of
        # anything a UI can read.
        config_path = workspace / "opencode.json"
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        # This file contains the conversation-scoped MCP bearer. Do not rely on
        # the process umask inside a volume shared by every conversation.
        config_path.chmod(0o600)

        files = project_workspace_files(
            project_id=project_id,
            guidance=guidance,
            material=material,
            material_texts=material_texts,
            docs_mounted=linked,
            memory=memory,
            mode=mode,
        )
        _write_projection(workspace, files)
        return linked, files

    try:
        docs_linked, written = await asyncio.to_thread(_write)
    except OSError as exc:
        logger.error(
            "Failed to prepare labs workspace %s for conversation %s: %s",
            workspace,
            conversation_id,
            exc,
            exc_info=True,
        )
        raise

    # Derived from what was written rather than tracked alongside it: the log
    # line is then a statement about the directory, not about a counter that can
    # disagree with it.
    projected = frozenset(file.path for file in written)
    guidance_layers = tuple(
        layer.value
        for layer, relative, _ in _LAYER_FILES
        if f"{_NEXUS_DIR}/{relative}" in projected
    )
    material_files = sum(
        1 for file in written if file.origin.startswith(ORIGIN_DOCUMENT_PREFIX)
    )
    memory_files = sum(
        1 for file in written if file.origin.startswith(ORIGIN_GENERATION_PREFIX)
    )

    logger.info(
        "labs.workspace conversation_id=%s dir=%s project_id=%s docs_mounted=%s "
        "guidance_layers=%s material_files=%s memory_files=%s memory_flagged=%s "
        "memory_key=%s",
        conversation_id,
        workspace,
        project_id or "-",
        docs_linked,
        ",".join(guidance_layers),
        material_files,
        memory_files,
        memory.flagged_count if memory is not None else 0,
        # The cache key is the one value that answers "did the memory change
        # between these two turns" without diffing the files.
        (memory.cache_key[:12] if memory is not None else "-") or "-",
    )
    return str(workspace)


__all__ = [
    "ORIGIN_ASSEMBLED",
    "ORIGIN_DOCUMENT_PREFIX",
    "ORIGIN_GENERATION_PREFIX",
    "ORIGIN_MANIFEST",
    "ORIGIN_ORG_LAYER",
    "ORIGIN_PERSON_LAYER",
    "ORIGIN_PROJECT_LAYER",
    "ORIGIN_SYSTEM_PROMPT",
    "ORIGIN_THREAD",
    "WorkspaceFile",
    "always_loaded_tokens",
    "ensure_labs_workspace",
    "labs_docs_mounted",
    "labs_workspace_path",
    "project_workspace_files",
]
