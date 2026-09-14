"""Turn-level prompt guidance for topology answers.

Kept out of the per-cohort system prompts because all three cohorts (read-only,
plan-control, effect) need the same rules, and the guidance is a function of the
turn (the page the user is on) rather than of the cohort. Each cohort assembly
appends the result of :func:`topology_turn_guidance` to its own prompt.

Which topology *tool* to call, and what to do with an ambiguous name, is not
here: that belongs to the capability contract in ``src.harness.topology_capability``
-- its ``use_when`` / ``do_not_use_when`` lists plus the registry-sourced
per-operation guide it renders into ``purpose``. That capability contract is the
only thing the harness turns into model-visible tool text; the descriptions in
``src.topology.registry`` reach the model on the legacy loop directly and on the
harness path only because the capability imports and renders them. (This comment
previously claimed the registry was "where the harness renders tool guidance",
which was false for the harness path and is why an operation could go unused.)

What is here is everything the capability contract cannot express -- source
honesty, unresolved-mapping honesty, per-stream attribution, navigation intent,
and the untrusted page hint.

One block of answer rules, both surfaces
----------------------------------------
`SHARED_ANSWER_GUIDANCE` is emitted everywhere; each surface then adds only what
is genuinely specific to it. The rules are shared because every one of them is
about whether a sentence is TRUE, and that does not vary by surface.

This replaced a split in which the canvas carried its own 1 679-word block and
`/chat` a 333-word one. The two overlapped heavily and had drifted, and the long
one was mostly prohibitions -- 22 of them under 12 shouted headings -- which in
practice bought terse, hedged answers rather than accurate ones. Three of its
rules were actively harmful and are gone rather than merged:

* a brevity instruction ("your answer may be rendered in a ~360px side panel...
  one line beats five bullets"), which clipped explanations the user had asked
  for;
* ~450 words of citation-suppression machinery, now enforced after the fact in
  `readonly_runtime._humanize_visible_content` where it cannot cost the model
  attention mid-answer;
* an instruction to exhaust a capped result page by page with `offset`. A real
  design has ~250 components against a listing cap of 12, so obeying it meant
  ~21 sequential model->tool->model rounds inside a 300s turn deadline. The turn
  hit the deadline, and `readonly_runtime` then discards everything it collected
  and substitutes a budget-stop message. `ANSWER FROM WHAT ONE READ RETURNS`
  replaces it: report the total the result carries, answer from the page you
  have, and page further only on demand.

Availability is decided elsewhere, and no longer by question shape. The topology
capability is exposed on the topology surface and withheld in plain chat, full
stop -- see ``src.harness.surface_policy``. This module only chooses how loudly
the surviving guidance speaks; nothing here gates a tool. The former
``topology_capability_enabled`` read the question text to decide, which meant a
structural question phrased outside its regexes silently lost the tool.
"""

from __future__ import annotations

import re
from typing import Any

#: Names carried by a topology level that identify a specific entity, in the
#: order the frontend `TopologyLevel` union declares them.
_PAGE_SCOPE_LABEL_KEYS = ("componentName", "jobName", "groupName")

_MAX_PROMPT_TOKEN_CHARS = 200

#: Prefixes the CANVAS adds to build a render key, which the topology graph
#: cannot resolve. `topologyLevelKey`/`selectionKey` in
#: `frontend/lib/topologyTargets.ts` compose `integration:<selection kind>:` in
#: front of an otherwise-valid identifier; the architecture canvas keys system
#: pairs as `pair:`. Stripping them here rather than in each canvas is
#: deliberate: this is the single place every id space converges, so a future
#: canvas inventing another render key cannot reintroduce the whole class of
#: bug (an unresolvable id, a `not_found`, and an answer improvised from the
#: label).
_UI_KEY_PREFIXES = (
    "integration:relationship:",
    "integration:application:",
    "integration:system:",
    "integration:edge:",
    "integration:group:",
    "integration:job:",
    "system:",
    "pair:",
)

#: How the architecture canvas joins the two halves of a system-pair key, and
#: how `selectionKey` joins a relationship's endpoints.
_PAIR_SEPARATORS = ("<->", "->")

#: The canvas sends a design's PATH-QUALIFIED id
#: (``<root>/<zip>/<group>::<design name>``) while the design index keys on the
#: bare name, and its substring tier tests needle-in-name rather than
#: name-in-needle -- so the long form can never match. The leaf after the last
#: separator is the form `get_job_detail` accepts.
_QUALIFIED_ID_SEPARATOR = "::"
_PATH_SEPARATOR = "/"

#: The surface whose questions are predominantly structural.
_TOPOLOGY_SURFACE = "topology"


#: Whitespace that is safe to collapse inside an IDENTIFIER. Deliberately not
#: `str.split()`, which additionally treats the C0 separators \x1c-\x1f as
#: whitespace -- and the canvas joins the parts of an edge key with US (\x1f).
#: Normalising an edge key with `split()` therefore rewrote
#: `business-stream<US>ds-...` as `business-stream ds-...`, which resolves to
#: nothing. Display text may still use `_prompt_token`; anything the model is
#: told to look something up with must not.
_LAYOUT_WHITESPACE_RE = re.compile(r"[ \t\r\n\v\f]+")
_LAYOUT_WHITESPACE = " \t\r\n\v\f"


def _prompt_token(value: Any, *, limit: int = _MAX_PROMPT_TOKEN_CHARS) -> str | None:
    """Return one whitespace-normalized prompt token from untrusted context."""

    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split()).strip()
    return normalized[:limit] or None


def _prompt_identifier(value: Any, *, limit: int = 500) -> str | None:
    """Return one bounded prompt token that is still usable as a lookup key.

    Same defensiveness and bounding as :func:`_prompt_token`; only layout
    whitespace is collapsed, so in-band separators survive intact.
    """

    if not isinstance(value, str):
        return None
    normalized = _LAYOUT_WHITESPACE_RE.sub(" ", value).strip(_LAYOUT_WHITESPACE)
    return normalized[:limit] or None


SHARED_ANSWER_GUIDANCE = (
    "SOURCE OWNERSHIP. The deterministic scan owns WHAT THE CODE DOES: which designs "
    "and components exist, how they are wired, which target columns are populated, and "
    "the expressions that compute them. Documentation owns WHY IT EXISTS: purpose, "
    "business meaning, and business policy. Runtime records and logs own present-tense "
    "claims about executions, failures, health, and timestamps. So 'what does this "
    "transform and how' is a structural question and 'why does this rule exist' is a "
    "documentation question, even when the user says 'business rule' for both. These "
    "sources complement one another; none silently substitutes for another.\n"
    "\n"
    "NEVER SUBSTITUTE A SOURCE SILENTLY. If a structural lookup returned nothing and "
    "you answer from documents or runtime data instead, say which source answered it "
    "AND that the structural artifact did not cover it. An unlabelled document answer "
    "to a structural question reads as established fact and is a defect, even when the "
    "document happens to be right. Honor explicit source instructions the same way: if "
    "the user excludes a source you need, disclose the unsupported portion or ask, "
    "rather than reaching for it quietly.\n"
    "\n"
    "SAY WHEN A SOURCE IS EMPTY. 'That is not described at this level' is a correct, "
    "useful, complete answer. Reaching for the next-best source is not.\n"
    "\n"
    "PRESENCE AND ABSENCE ARE LOOKUPS, NOT JUDGEMENTS. Whether a system, design or "
    "field exists is decided by a tool result and by nothing else. Never write that "
    "something 'appears in the landscape', 'exists but is not connected', or 'is "
    "present elsewhere' unless a call returned it. Inventing presence is harder to "
    "catch than inventing a relationship, because a reader checking the diagram will "
    "conclude the diagram is incomplete rather than that the answer is wrong.\n"
    "\n"
    "NEVER EXPLAIN A NAME YOU DID NOT READ. If the user types an entity name that no "
    "result contains -- misremembered, abbreviated, or mistyped -- do not gloss it and "
    "do not describe what that kind of name usually means. Say you cannot find it and "
    "offer the closest names that do exist. A plausible explanation of a name that does "
    "not exist is a fabrication even when every general statement in it is true.\n"
    "\n"
    "SAY WHAT IS UNCERTAIN, IN PLAIN WORDS. A stream the scan could only infer, a "
    "business name a person wrote rather than the scanner reading it out of the code, "
    "an overlay that is stale -- name that weakness in ordinary language: 'this one is "
    "inferred: the scan can see traffic between them but not what it carries'. Silence "
    "here is a defect, because an inferred relationship then reads exactly like a "
    "confirmed one. Facts read straight from the code need no caveat.\n"
    "\n"
    "CONFIDENCE IS PER ROW, AND SO IS ATTRIBUTION. Never write 'all streams are "
    "confirmed' unless every row you listed carries that state; count them instead -- "
    "'five confirmed, one proposed'. One relationship between two systems is routinely "
    "several streams carried by DIFFERENT integrations, so attribute per stream. A "
    "single-carrier headline above a table whose rows have different carriers is worse "
    "than no headline, because a reader trusts the headline over the table.\n"
    "\n"
    "A DESIGN IS NOT A COMPONENT. A design is a whole job or route -- "
    "'AssetCheckMessageHandler'. A component is one box inside a design -- 'tMap_1'. "
    "Names that implement a relationship are designs; call them jobs or routes.\n"
    "\n"
    "MAPPINGS: ABSENCE AND UNRESOLVED ARE DIFFERENT THINGS. When a component reports "
    "no published column-level mapping, say exactly that -- not that it does not "
    "transform anything. And roughly 39% of field-lineage entries across this corpus "
    "are decided at runtime rather than written in the code, so state both numbers when "
    "you present mappings: 'of 41 target fields, 25 have a resolved expression and 16 "
    "are decided at runtime'. Presenting only the resolved subset reads as a complete "
    "mapping and understates the integration.\n"
    "\n"
    "ANSWER FROM WHAT ONE READ RETURNS. Structural results are capped and report their "
    "own totals. Say the total, describe what you read, and stop -- 'this design has "
    "251 components; the main ones are ...'. Do not walk a result page by page to "
    "exhaust it: the turn has a deadline, and an answer that never arrives is worth "
    "less than an honest partial one. Read a further page only when the user asks for "
    "something a later page specifically holds.\n"
    "\n"
    "LABEL A MIXED ANSWER BY ORIGIN -- '(from logs)' for runtime results, '(from <doc "
    "name>)' for document claims -- so a live observation cannot be mistaken for "
    "published structure, or the reverse. When a project has no published topology "
    "yet, say it needs a rescan: that is a missing artifact, not a failure, and never "
    "a reason to describe structure you did not read."
)


NAVIGATION_INTENT_GUIDANCE = (
    "Navigation intent: every topology call takes navigation_intent, and every "
    "target it returns is stamped with it.\n"
    "  'navigate' — ONLY when the user explicitly asked to be taken somewhere: "
    "'take me to...', 'open...', 'go back to the view between X and Y', 'show me "
    "that job'. The user's canvas will move.\n"
    "  'reference' — everything else, and the default. Use it whenever you are "
    "merely naming, explaining, or listing entities, even if the user mentioned one "
    "by name. Mentioning an entity is not a request to go there.\n"
    "If you are unsure, use 'reference': a wrongly-navigating answer moves the "
    "diagram out from under the user mid-question, which is worse than a chip they "
    "have to click."
)


#: Appended in ``/chat`` only, where the structural reader is NOT exposed (see
#: ``src.harness.surface_policy``). It names the boundary and makes handing the
#: user to the canvas the sanctioned move rather than a failure. Keep it short:
#: it is a boundary notice, and the answer rules it sits beside are shared.
#:
#: This text must never instruct the model to "gather topology first". It used
#: to, which became an instruction to use a tool that is not in the list -- the
#: reliable way to get a model to improvise the structure it was told to look up.
CHAT_TOPOLOGY_BOUNDARY = (
    "You cannot read the project's published topology on this surface. Complete "
    "inventory and counts, what connects to what, dependency traversal, which jobs or "
    "components implement a flow, and column-level field mappings all live in that "
    "artifact, and the tool that reads it is deliberately not available here -- it is "
    "available on the topology page, where the user can see the diagram the answer "
    "refers to.\n"
    "So for a structural question: answer whatever indexed documentation genuinely "
    "supports, say plainly which part you could not verify, and point the user to the "
    "topology page for the structural detail. That referral is a correct and complete "
    "answer, not an apology -- do not pad it with speculation to look more helpful, "
    "and never infer the structure to fill the gap."
)


def _resolvable_identity(raw: Any) -> str | None:
    """One identifier a topology read can actually match, or ``None``.

    Undoes the two transforms the canvas applies for its own bookkeeping and
    which are the difference between a resolved subject and a `not_found`:
    the `integration:<kind>:` / `pair:` render prefixes, and the path
    qualification on a design id. Order matters -- the prefix is stripped first,
    because `integration:edge:business-stream<US>ds-...` carries both a prefix
    and a real key.

    Returns ``None`` for a value that survives as a composite (a system PAIR is
    two identifiers, not one); `_pair_endpoints` handles that case, so emitting
    half of it here would name the wrong endpoint.
    """

    token = _prompt_identifier(raw)
    if token is None:
        return None
    for prefix in _UI_KEY_PREFIXES:
        if token.lower().startswith(prefix):
            token = token[len(prefix) :]
            break
    if any(separator in token for separator in _PAIR_SEPARATORS):
        return None
    # The leaf is taken ONLY from a path-qualified id. `table::column` and
    # `job::component` use the same separator without a path, and both halves
    # are load-bearing there -- reducing `output::Status` to `Status` would
    # discard the table and point a mapping question at the wrong column.
    if _PATH_SEPARATOR in token and _QUALIFIED_ID_SEPARATOR in token:
        token = token.rsplit(_QUALIFIED_ID_SEPARATOR, 1)[-1]
    return _prompt_identifier(token)


def _pair_endpoints(raw: Any) -> tuple[str, str] | None:
    """The two system identifiers inside a canvas pair key, or ``None``.

    `pair:system:sys-afas<->system:sys-maximo` and
    `relationship:sys-a->sys-b` both name two entities. Neither resolves as one
    string, and both endpoints resolve individually, so the honest rendering is
    both names rather than a guess at which one the user means.
    """

    token = _prompt_identifier(raw)
    if token is None:
        return None
    for prefix in _UI_KEY_PREFIXES:
        if token.lower().startswith(prefix):
            token = token[len(prefix) :]
            break
    for separator in _PAIR_SEPARATORS:
        if separator not in token:
            continue
        head, _, tail = token.partition(separator)
        source = _resolvable_identity(head)
        target = _resolvable_identity(tail)
        if source and target:
            return source, target
    return None


def _describe_level(level: dict[str, Any], kind: str) -> str:
    """What the canvas is showing, in the user's terms rather than the enum's.

    The enum alone misdescribes two of the five levels badly enough to change
    the answer. `project` is the level for BOTH the whole landscape and a
    single focused system (selecting a node does not push a navigation level),
    and `integration` is the level for both one data stream and the
    implementation detail underneath it. Rendering the bare kind is what told
    the model "the user is viewing the project level" while one system was
    selected -- and a project-level answer is then the obedient response.
    """

    named = next(
        (
            _prompt_token(level[key])
            for key in _PAGE_SCOPE_LABEL_KEYS
            if _prompt_token(level.get(key)) is not None
        ),
        None,
    )
    if kind == "project":
        return "the landscape: every system in the project and the streams between them"
    if kind == "integration":
        selection = level.get("selection")
        selection_kind = (
            _prompt_token(selection.get("kind")) if isinstance(selection, dict) else None
        )
        if selection_kind == "edge":
            return (
                "one data stream, drilled in: the systems at each end and the designs "
                "(jobs and routes) that implement it"
            )
        if selection_kind == "relationship":
            return "the aggregate relationship between two systems and its constituent streams"
        if selection_kind in {"system", "application"}:
            return "one system and the streams that enter and leave it"
        return "one integration, drilled in below the landscape"
    if kind == "group" and named:
        return f"group '{named}' and the designs inside it"
    if kind == "job" and named:
        return f"design '{named}' and its components"
    if kind == "component" and named:
        job = _prompt_token(level.get("jobName"))
        inside = f" inside design '{job}'" if job else ""
        return f"the column-level mapping of component '{named}'{inside}"
    return f"the {kind} level"


def _describe_selection(page_scope: dict[str, Any], level: dict[str, Any]) -> str:
    """The single object the user has picked out, as an instruction to use it.

    Reads BOTH `selected_entity` and `level.selection`. The latter used to be
    dropped entirely, which meant a drilled-in data stream arrived with its own
    resolvable key -- `business-stream<US>ds-...`, already in the payload --
    unavailable to the model, while the only id forwarded was that same key
    behind an unresolvable prefix.

    When nothing is selected this says so explicitly. Silence is what let a
    previously discussed entity be reported as the current selection: an absent
    statement reads as no information, and the conversation history is then the
    only thing describing a selection.
    """

    lines: list[str] = []
    selected = page_scope.get("selected_entity")
    if isinstance(selected, dict):
        kind = _prompt_token(selected.get("kind"))
        label = _prompt_token(selected.get("label"))
        if kind and label:
            raw_id = selected.get("entity_id")
            identity = _resolvable_identity(raw_id)
            endpoints = _pair_endpoints(raw_id)
            if endpoints:
                lines.append(
                    f"SELECTED ON SCREEN: the {kind} between '{endpoints[0]}' and "
                    f"'{endpoints[1]}', shown as '{label}'. Both endpoints resolve "
                    "individually; the pair is not a single entity."
                )
            elif identity and identity.casefold() != label.casefold():
                lines.append(
                    f"SELECTED ON SCREEN: the {kind} '{label}'. Look it up as "
                    f"'{identity}'; if that returns nothing, look up '{label}'."
                )
            else:
                lines.append(f"SELECTED ON SCREEN: the {kind} '{label}'.")

    selection = level.get("selection")
    if isinstance(selection, dict):
        selection_kind = _prompt_token(selection.get("kind"))
        if selection_kind == "edge":
            edge_key = _prompt_identifier(selection.get("edgeKey"))
            if edge_key:
                lines.append(
                    "The drilled-in data stream is identified by "
                    f"'{edge_key}' -- pass that value verbatim to look it up."
                )
        elif selection_kind == "relationship":
            source = _prompt_identifier(selection.get("source"))
            target = _prompt_identifier(selection.get("target"))
            if source and target:
                lines.append(
                    f"The drilled-in relationship runs between '{source}' and '{target}'."
                )
            streams = selection.get("streamKeys")
            if isinstance(streams, list):
                keys = [
                    key
                    for key in (_prompt_identifier(item) for item in streams[:12])
                    if key
                ]
                if keys:
                    lines.append(
                        f"It is carried by {len(keys)} stream(s): {', '.join(keys)}."
                    )
        else:
            identity = _resolvable_identity(
                selection.get("systemId")
                or selection.get("applicationId")
                or selection.get("edgeKey")
            )
            if identity:
                lines.append(f"The drilled-in entity is identified by '{identity}'.")

    if not lines:
        lines.append(
            "NOTHING IS SELECTED on screen. The whole view above is the subject. Do "
            "not describe any entity discussed earlier in this conversation as the "
            "current selection -- there is none."
        )
    return " ".join(lines)


def describe_page_scope(page_scope: Any) -> str | None:
    """Render an untrusted page scope as the turn's subject, or ``None``.

    Read defensively: the value originates from the client, so every field is
    treated as optional and length-bounded here rather than trusted to be
    well-formed. It is a prompt hint only — never a retrieval filter, an
    authorization decision, or a widening of project access. A page scope naming
    an inaccessible project therefore grants nothing: the turn's authorized
    project scope is unchanged by it.

    What it IS, though, is authoritative about one thing: which object a
    demonstrative question is about. The previous wording named the selection in
    one clause and then spent two sentences de-weighting it ("a PREFERENCE, not
    a restriction"), inside several thousand words of answer rules -- and the
    observed behaviour was a project-wide answer with the selected system
    mentioned as a parenthetical, or a request that the user name the object
    already named in the payload. Scope-as-subject and scope-as-filter are
    different claims: the first is stated firmly below, the second is still
    refused.
    """

    if not isinstance(page_scope, dict):
        return None
    project_id = _prompt_token(page_scope.get("project_id"))
    level = page_scope.get("level")
    if project_id is None or not isinstance(level, dict):
        return None
    kind = _prompt_token(level.get("kind"))
    if kind is None:
        return None

    details: list[str] = []
    active_tab = _prompt_token(page_scope.get("active_detail_tab"), limit=100)
    if active_tab:
        details.append(f"Open detail tab: '{active_tab}'.")
    filters = page_scope.get("filters")
    rendered_filters: list[str] = []
    if isinstance(filters, list):
        for item in filters[:12]:
            if not isinstance(item, dict):
                continue
            name = _prompt_token(item.get("name"), limit=100)
            value = _prompt_token(item.get("value"))
            if name and value:
                rendered_filters.append(f"{name}={value}")
    if rendered_filters:
        details.append(f"Visible filters: {', '.join(rendered_filters)}.")
    trailer = f"\n{' '.join(details)}" if details else ""

    return (
        f"PAGE CONTEXT — the user is on the topology canvas of project '{project_id}'.\n"
        f"On screen: {_describe_level(level, kind)}.\n"
        f"{_describe_selection(page_scope, level)}{trailer}\n"
        "THE SELECTION IS THE SUBJECT. 'this', 'here', 'that job', 'these jobs', "
        "'this design', 'this connection' and 'what am I looking at' all refer to what "
        "is selected on screen above — never to an entity named earlier in the "
        "conversation. Answer about it, at the depth the view is showing: a drilled-in "
        "stream is a question about its implementing designs and their mappings, not a "
        "repeat of the landscape summary. Never ask the user which object they mean "
        "when the selection above names one; reading it is your job, not theirs. If an "
        "identifier above returns nothing, look the label up by name and say so if it "
        "still cannot be found — do not fall back to the level above and present that "
        "as the answer.\n"
        "This fixes the SUBJECT, not your SOURCES or your REACH. A question that names "
        "something else, spans the whole project, crosses projects, or asks about "
        "runtime and logs is answered normally. It grants no access to any project you "
        "are not already authorized for."
    )


def is_topology_surface(page_scope: Any) -> bool:
    """Whether this turn was asked from the topology canvas.

    Untrusted client input. It selects which capabilities are OFFERED (see
    ``src.harness.surface_policy``) and how prominent the guidance is; it is
    never an authorization decision. Project scope and permissions remain the
    only authority over what a surviving capability may read, so a forged value
    changes the tool list and reaches no data its holder is not entitled to.
    """

    return isinstance(page_scope, dict) and page_scope.get("surface") == _TOPOLOGY_SURFACE


def topology_turn_guidance(page_scope: Any = None) -> str:
    """Compose the guidance appended to a cohort's system prompt for one turn.

    One shared block of answer rules on both surfaces, plus what is specific to
    each: navigation intent on the canvas (it governs the targets only the canvas
    can act on), the boundary notice in ``/chat`` (the structural reader is not
    offered there). The page context goes LAST wherever it exists.

    Two things about this shape are deliberate and were both learned the hard
    way. The rules are shared because they are about whether a sentence is TRUE,
    which does not vary by surface -- the previous split maintained two
    overlapping copies and the canvas one had grown to five times the size of
    the other, mostly prohibitions, which bought terse defensive answers rather
    than accurate ones. And the page hint goes last because last means nearest
    the question: when it led, answers came back scoped to the level above the
    selection with the selected entity demoted to a parenthetical.
    """

    sections: list[str] = [SHARED_ANSWER_GUIDANCE]
    if is_topology_surface(page_scope):
        sections.append(NAVIGATION_INTENT_GUIDANCE)
    else:
        sections.append(CHAT_TOPOLOGY_BOUNDARY)
    hint = describe_page_scope(page_scope)
    if hint is not None:
        sections.append(hint)
    return "\n\n".join(sections)


__all__ = [
    "CHAT_TOPOLOGY_BOUNDARY",
    "NAVIGATION_INTENT_GUIDANCE",
    "SHARED_ANSWER_GUIDANCE",
    "describe_page_scope",
    "is_topology_surface",
    "topology_turn_guidance",
]
