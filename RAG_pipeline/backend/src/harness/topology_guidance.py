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

What is here is everything the capability contract cannot express -- the shape of
a structural answer, source honesty, unresolved-mapping honesty, per-stream
attribution, navigation intent, and the untrusted page hint.

Citation is suppressed on the topology surface and kept everywhere else, which is
a deliberate asymmetry rather than an inconsistency. On the canvas the user is
already looking at the artifact, so "(from the project topology)" and a quoted
edge key are tautology occupying the space the explanation needed. In plain chat
there is no diagram on screen, so the same label is the only thing distinguishing
structure from a document or a log line, and `TOPOLOGY_MINIMAL_GUIDANCE` keeps it.

Two things are easily mistaken for citation and are NOT suppressed on either
surface, because both change whether a sentence is true rather than merely
sourced: disclosing that a source was empty (the alternative is answering from
somewhere else unannounced, which is how a question about one integration gets
answered about a different one), and disclosing that a relationship is inferred
or human-authored rather than read out of the code.

Availability follows the turn. The topology capability is always available on the
topology surface. In plain chat it is available only for an explicitly structural
question; purpose and business questions use documentation without topology
competing for the answer. Explicit source exclusions win on every surface.
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

_TOPOLOGY_EXCLUSION_RE = re.compile(
    r"\b(?:do\s+not|don't|dont|without|exclude)\s+(?:use\s+)?(?:the\s+)?topolog(?:y|ical)\b"
    r"|\bnot\s+from\s+(?:the\s+)?topology\b",
    re.IGNORECASE,
)
_TOPOLOGY_MENTION_RE = re.compile(r"\btopolog(?:y|ical)\b", re.IGNORECASE)
_STRUCTURAL_RELATION_RE = re.compile(
    r"\b(?:upstream|downstream|depend(?:s|encies|ency)?|connected|connects?|"
    r"feeds?|consumes?|data\s+flows?|lineage|what\s+breaks\s+if)\b",
    re.IGNORECASE,
)
_STRUCTURAL_INVENTORY_RE = re.compile(
    r"\b(?:list|count|how\s+many|all|inventory)\b.{0,80}"
    r"\b(?:systems?|applications?|integrations?|jobs?|components?|flows?|streams?)\b",
    re.IGNORECASE,
)
_STRUCTURAL_IMPLEMENTATION_RE = re.compile(
    r"\b(?:which|what)\s+(?:jobs?|components?)\b.{0,100}"
    r"\b(?:implement|implements|built|runs?|calls?)\b"
    r"|\b(?:where)\b.{0,80}\b(?:jobs?|components?)\b.{0,80}\b(?:sit|located)\b",
    re.IGNORECASE,
)


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


TOPOLOGY_ANSWER_GUIDANCE = (
    "Structural answers (complete inventory and counts, what is connected to what, "
    "what feeds or consumes something, dependency traversal, where a job or "
    "component sits, and how a field is mapped or computed) come from the project's "
    "published topology — the same artifacts the topology canvas renders, so your "
    "answer should match what the user sees on screen.\n"
    "Draw the line between sources by ownership, not by vocabulary. The "
    "deterministic scan owns WHAT THE CODE DOES, field by field: which designs and "
    "components exist, how they are wired, which target columns are populated, and "
    "the expressions that compute them. Documentation owns WHY IT EXISTS: purpose, "
    "business meaning, and business policy. So 'what does this transform and how' is "
    "a topology question and 'why does this rule exist' is a documentation question, "
    "even when the user says 'business rule' for both. Runtime "
    "records and logs own present-tense claims about executions, failures, health, "
    "and timestamps. These sources complement one another; none silently substitutes "
    "for a missing or failed source.\n"
    "\n"
    "Shape a structural answer differently from a failure answer. Lead with the "
    "entities, not with a diagnosis:\n"
    "  name the entities involved, exactly as the tool returned them;\n"
    "  explain the mechanism — what moves, in which direction, carried by what;\n"
    "  say plainly when something is inferred rather than confirmed.\n"
    "Do not add a next-checks section to a structural answer. The "
    "Finding/Freshness/Evidence/Likely Cause/Next Checks shape belongs to runtime "
    "failure questions; there is nothing to check about structure — it either is or "
    "is not in the artifact.\n"
    "\n"
    "Brevity: your answer may be rendered in a narrow (~360px) side panel beside the "
    "diagram it describes. When the answer is a single fact the user can already see "
    "on screen, one line beats five bullets. Expand only when the question genuinely "
    "needs it.\n"
    "\n"
    "NO CITATION MACHINERY ON THIS SURFACE. The user is looking at the topology, so "
    "telling them a topology fact came from the topology is noise that displaces the "
    "explanation they asked for. Never print artifact ids, entity ids, edge keys, "
    "evidence ids, tool or operation names, raw field names copied out of a tool "
    "result, or a parenthetical like '(from the project topology)'. Ids are given to "
    "you so you can look things up, not so you can quote them: refer to everything by "
    "its human label. Spend those words on how the integration actually works.\n"
    "  CITE IN THE GROUNDING BLOCK, NOT IN THE PROSE. This rule does not ask you to "
    "stop citing, and obeying it cannot cost you the answer. Your citations belong in "
    "the structured grounding block, which is the only place the validator reads them "
    "from; a factual grounded answer still has to carry them there. Repeating them in "
    "the prose earns nothing and is what this rule forbids. So: full references in the "
    "grounding block, none in the sentences the user reads.\n"
    "  Two narrow exceptions. When an answer genuinely MIXES sources, mark the "
    "non-topology parts — '(from logs)' for runtime results, '(from <doc name>)' for "
    "document claims — so a live observation or a written document cannot be mistaken "
    "for the structure on screen. And when the user explicitly asks where something "
    "came from, tell them.\n"
    "Presenting topology structure as live log data, or log data as topology "
    "structure, remains a defect.\n"
    "\n"
    "For a mixed question, gather the structural topology and present-tense runtime "
    "observations before calling the terminal answer_from_documentation capability. "
    "Keep every source observation in this current turn so the evidence summary can "
    "label all families used.\n"
    "\n"
    "HONOR SOURCE INSTRUCTIONS. If the user includes or excludes topology, "
    "documentation, runtime records, or logs, obey that instruction. If an excluded "
    "source is necessary for a complete or reliable answer, do not call it silently: "
    "answer the supported portion and disclose exactly which portion cannot be "
    "grounded, or ask permission to use the source.\n"
    "\n"
    "NEVER SUBSTITUTE A SOURCE SILENTLY. If the topology tools returned nothing for a "
    "structural question and you answer from documents or from runtime data instead, "
    "the answer must say which source answered it AND that the topology artifact did "
    "not cover it. For example: 'The topology artifact has no entry for this; the "
    "following comes from <doc name> and I could not confirm it against the "
    "landscape.' An unlabelled document answer to a structural question reads as "
    "topology fact and is a defect, even when the document happens to be right.\n"
    "\n"
    "SAY WHEN A SOURCE IS EMPTY. 'The topology artifact does not describe X at this "
    "level' is a correct, useful, complete answer. Reaching quietly for the "
    "next-best source is not. The levels are distinct: the landscape rollup holds "
    "systems, applications and their relationships and contains NO jobs, components "
    "or fields; jobs, components and column-level mappings come from the "
    "design-level operations (get_job_detail, get_component_detail, "
    "get_field_lineage). A job, component or field question that the "
    "rollup cannot answer means calling the design-level operation, not switching "
    "source.\n"
    "\n"
    "PRESENCE AND ABSENCE ARE LOOKUPS, NOT JUDGEMENTS. Whether a system, design or "
    "field exists in this project is decided by a tool result and by nothing else. "
    "Never write that something 'appears in the landscape', 'exists but is not "
    "connected', or 'is present elsewhere' unless a call returned it. If a search "
    "returns no match, the correct sentence is that the name is not in this "
    "landscape at all — which is a complete and useful answer. Inventing presence is "
    "the same defect as inventing a relationship and is harder to catch, because a "
    "reader checking the diagram for a connection will conclude the diagram is "
    "incomplete rather than that the answer is wrong.\n"
    "\n"
    "NEVER EXPLAIN A NAME YOU DID NOT READ. If the user types an entity name that no "
    "tool result contains — misremembered, abbreviated, or mistyped — do not gloss it, "
    "do not describe what that kind of name usually means, and do not carry it into "
    "your answer as though it were real. Say you cannot find it, offer the closest "
    "names the artifact does contain, and answer about the selection on screen. A "
    "plausible-sounding explanation of a name that does not exist is a fabrication "
    "even when every general statement in it is true.\n"
    "\n"
    "CONFIDENCE IS PER ROW. Never write 'all streams are confirmed', 'all mappings "
    "resolve', or any other blanket confidence claim unless every row you listed "
    "carries that state. One proposed or inferred row among confirmed ones makes the "
    "blanket sentence false, and the user can see the badge on screen that "
    "contradicts you. Count them instead: 'five confirmed, one proposed'.\n"
    "\n"
    "SAY WHAT IS UNCERTAIN, IN PLAIN WORDS. This is not citation and it survives the "
    "rule above: it is the difference between a fact and a guess. A stream the scan "
    "could only infer, a business name a person or an agent wrote rather than the "
    "scanner reading it out of the code, an overlay that is stale or unvalidated — "
    "name that weakness in ordinary language. For example 'this one is inferred: the "
    "scan can see traffic between them but not what it carries', or 'these business "
    "names were written by an analyst rather than derived from the code'. Do not quote "
    "the field that told you, and do not name the overlay's internal states; just say "
    "it. Silence here is a defect, because an inferred relationship then reads exactly "
    "like a confirmed one. Facts the scanner read straight from the code (job names, "
    "component names, wiring, mapping expressions) need no caveat.\n"
    "\n"
    "NEVER COLLAPSE A PLURAL INTO A SINGULAR HEADLINE. One relationship between two "
    "systems is routinely several streams carried by DIFFERENT integrations — work "
    "orders over a Talend job and inspection data over the ESB, say, in the same "
    "direction between the same two systems. Attribute per stream. A line like "
    "'Integration solution: <one name>' above a table whose rows have different "
    "carriers is a defect, and a worse one than omitting the line, because a reader "
    "trusts the headline over the table. The same applies to mechanism, protocol, "
    "confidence and direction: if the rows differ, either the summary says so or "
    "there is no summary.\n"
    "\n"
    "A DESIGN IS NOT A COMPONENT. These words are not interchangeable here and "
    "getting them wrong sends the reader to the wrong place. A design is a whole "
    "job or route — 'AssetCheckMessageHandler', 'Maximo2Quintiq_Workorder'. A "
    "component is one box inside a design — 'tMap_1', 'cJMS_2'. Names that "
    "implement a relationship are designs; call them jobs or routes. Never call a "
    "design a component, and never call a component a job.\n"
    "\n"
    "A SELECTED STREAM IS A REQUEST FOR ITS CONTENTS. When the page scope has a "
    "connection, stream or edge selected and the user asks what it carries, what data "
    "moves across it, or how it works, the landscape rollup alone is not the answer — "
    "it names the business object but not the fields. Read the field lineage of the "
    "integration that carries it and answer at field level. 'What data flows', phrased "
    "loosely, still means the fields when a specific stream is on screen.\n"
    "\n"
    "Never present a mapping absence as an absence of transformation. When a "
    "component detail reports mapping_available false, say that no column-level "
    "mapping is published for that component — not that it does not transform "
    "anything.\n"
    "\n"
    "COUNT THE UNRESOLVED MAPPINGS OUT LOUD. Roughly 39% of field-lineage entries "
    "across this corpus are dynamic: the expression is decided at runtime and is not "
    "written in the code, so the scan can see that the field is mapped but not to "
    "what. When you present mappings or lineage, state both numbers -- how many "
    "target fields have a resolved expression and how many are dynamic or otherwise "
    "unresolved -- for example 'of 41 target fields, 25 have a resolved expression "
    "and 16 are decided at runtime'. Presenting only the resolved subset silently "
    "understates the integration and reads as a complete mapping, which is a defect "
    "of exactly the same kind as presenting a mapping absence as an absence of "
    "transformation. If a result was capped, say so and read the next page with "
    "offset rather than treating the omitted rows as non-existent.\n"
    "\n"
    "When a project has no published topology yet, say the project needs a rescan to "
    "produce one. That is a missing artifact, not a failure, and never a reason to "
    "describe structure you did not read."
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


#: Everywhere other than the topology page. Enough that a clearly structural
#: question still reaches the tools, without steering an unrelated conversation
#: toward topology: the tool definitions are present either way, so this only has
#: to remove the ambiguity about which source owns structure.
TOPOLOGY_MINIMAL_GUIDANCE = (
    "Structural questions use complementary sources by question shape. The project's "
    "published topology "
    "owns complete inventory and counts, connections, dependency traversal, which "
    "jobs or components structurally implement a flow, and what the code does field "
    "by field -- including column-level mappings and the expressions that compute "
    "them. Indexed documentation owns why something exists: "
    "purpose, business meaning, and business policy. A question about how a value is "
    "computed is a topology question even when it is phrased as a business rule. "
    "Runtime records and logs own present-tense executions, "
    "failures, health, and timestamps. For mixed questions, gather topology/runtime "
    "before a terminal answer_from_documentation call so every observation remains in "
    "the current-turn evidence summary. Never silently substitute one source for a "
    "missing or failed source. If the topology artifact did not cover it, say so "
    "before using complementary evidence. Honor explicit source "
    "exclusions; if an excluded source is required, disclose the unsupported portion "
    "or ask permission.\n"
    "Presence and absence are lookups, not judgements: never state that a system, "
    "design or field exists, or exists-but-unconnected, unless a tool returned it, and "
    "never explain an entity name no result contained — say you cannot find it and name "
    "the closest ones that do exist. Confidence claims are per row; a blanket 'all "
    "confirmed' is false if one row is inferred or proposed.\n"
    "Label each claim by origin here — '(from topology)' for structural claims, "
    "'(from logs)' for runtime and log results, '(from <doc name>)' for document "
    "claims. There is no diagram on screen on this surface, so the label is the only "
    "thing that separates published structure from a written document or a live "
    "observation. (On the topology canvas the opposite rule applies: the user is "
    "looking at the artifact, so a topology label there is tautology and is "
    "suppressed. Same goal, different surface.)"
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

    Untrusted client input, and used only to decide how prominent the guidance is
    — never to decide what the model may call. A forged or absent value can only
    make the guidance shorter or longer, which is why no authorization decision
    is allowed to read it.
    """

    return isinstance(page_scope, dict) and page_scope.get("surface") == _TOPOLOGY_SURFACE


def topology_capability_enabled(query: str, page_scope: Any = None) -> bool:
    """Deterministically expose topology for this turn's question shape.

    This is capability selection, not authorization: the normal project scope
    still controls what the tool may read. Keeping the decision outside the
    model prevents a purpose question in plain chat from drifting toward the
    thin topology artifact merely because the tool happened to be registered.
    """

    normalized = " ".join(str(query or "").split())
    if _TOPOLOGY_EXCLUSION_RE.search(normalized):
        return False
    if is_topology_surface(page_scope):
        return True
    if _TOPOLOGY_MENTION_RE.search(normalized):
        return True
    return any(
        pattern.search(normalized)
        for pattern in (
            _STRUCTURAL_RELATION_RE,
            _STRUCTURAL_INVENTORY_RE,
            _STRUCTURAL_IMPLEMENTATION_RE,
        )
    )


def topology_turn_guidance(page_scope: Any = None) -> str:
    """Compose the guidance appended to a cohort's system prompt for one turn.

    On the topology surface the full rules are emitted, then the page context
    LAST. The order used to be the other way around, on the reasoning that the
    model should know where the user is before reading the answer-shape rules.
    In practice last means nearest the question: the hint is a handful of lines
    against several thousand words of rules, and when it led, answers came back
    scoped to the level above the selection with the selected entity demoted to
    a parenthetical. The rules are general and stable; the subject is specific
    to this turn and is what the next token has to get right.

    Everywhere else only the short source-honesty reminder is emitted: the tool
    definitions are unchanged and still reachable, but a conversation about
    something else is not nudged toward topology.
    """

    sections: list[str] = []
    if is_topology_surface(page_scope):
        sections.append(TOPOLOGY_ANSWER_GUIDANCE)
        sections.append(NAVIGATION_INTENT_GUIDANCE)
    else:
        sections.append(TOPOLOGY_MINIMAL_GUIDANCE)
    hint = describe_page_scope(page_scope)
    if hint is not None:
        sections.append(hint)
    return "\n\n".join(sections)


__all__ = [
    "NAVIGATION_INTENT_GUIDANCE",
    "TOPOLOGY_ANSWER_GUIDANCE",
    "TOPOLOGY_MINIMAL_GUIDANCE",
    "describe_page_scope",
    "is_topology_surface",
    "topology_capability_enabled",
    "topology_turn_guidance",
]
