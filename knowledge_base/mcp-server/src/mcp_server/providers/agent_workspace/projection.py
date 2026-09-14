"""Strip verbatim code out of approved parser JSON, and say that it was stripped.

The scanner is the trusted parser and is allowed to read source. The
documentation agent is not, and the source boundary is enforced on BYTES
rather than on filenames: keeping ``.item`` and ``scan/sanitized/`` out of the
workspace does nothing about the tJava body that the parser copied into
``components[].code`` and the routine source it copied into
``routines[].source_code``. Those shards were staged with ``shutil.copy2``, so
the agent received the code anyway -- through the one path nobody was
checking.

Two rules, and the whole module is the second one:

* **Remove, never truncate.** No snippet, no first-N-characters preview, no
  "the body starts with". A prefix of a credential is still a credential, and
  a prefix of a business rule is worse than no rule at all because it reads
  like the whole one.
* **State the removal where the reader is.** A missing ``code`` key is
  indistinguishable from a component that never had one, and the verified
  Steven failure is exactly that inference running the wrong way: generated
  documentation asserting configuration was unavailable while the scan held it
  all along. Every removal leaves a ``code_withheld`` block carrying the size,
  a sha256 of the body, and the identifiers the body named.

What deliberately STAYS, because it is parser structure rather than source
text: component types, settings-shaped parameters (queue names, tables,
endpoints, ``QUERY``), ``schemas[]``, ``connections[]``, and
``tmap_data`` / ``txmlmap_data`` including ``entries[].expression``. A mapping
expression is extracted per field by the parser and verified by the same
redaction pass as everything else; it IS the field-level documentation, and
removing it would leave the lane unable to answer the question it exists for.
``tests/test_agent_workspace/test_projection.py`` holds the two apart
explicitly.

The projection is a generic recursive walk rather than a per-artifact special
case. A future scanner field that carries a body under one of these names is
then withheld on the day it appears, instead of on the day somebody notices.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from mcp_server.providers.agent_workspace.contract import (
    CODE_PARAM_NAMES,
    VERBATIM_CODE_FIELDS,
)

#: The block that replaces a removed body. Named here because the workspace
#: builder, the disclosure renderer and the tests all have to agree on it.
WITHHELD_KEY = "code_withheld"

WITHHELD_REASON = (
    "verbatim source code is not published to the documentation lane; "
    "this block states what was removed"
)

#: At most this many distinct identifiers per kind travel with a summary. A
#: pathological generated body could otherwise produce a single JSON object
#: larger than a navigation shard.
_MAX_REFERENCES = 100

#: An identifier, and nothing else. Applied to every captured reference --
#: including a ``globalMap`` key, which is a string literal in the body and so
#: is the one capture that could in principle carry something that is not a
#: name. Anything that does not match is dropped rather than published.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]{0,79}$")

_ROUTINE_RE = re.compile(r"\broutines\.([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)")
_CONTEXT_RE = re.compile(r"\bcontext\.([A-Za-z_][A-Za-z0-9_]*)")
_GLOBAL_MAP_RE = re.compile(r"\bglobalMap\s*\.\s*(?:get|put)\s*\(\s*\"([^\"\\]{1,80})\"")

_REFERENCE_KINDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("routines", _ROUTINE_RE),
    ("context_variables", _CONTEXT_RE),
    ("global_map_keys", _GLOBAL_MAP_RE),
)

# Deliberately coarse semantic signals.  These are extracted while the trusted
# projection still has the body, then only labels and counts cross the source
# boundary.  No captured source fragment or arbitrary literal is retained.
_CONTROL_FLOW_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("conditional", re.compile(r"\bif\s*\(|\belse\b")),
    ("multi_branch", re.compile(r"\bswitch\s*\(|\bcase\b")),
    ("iteration", re.compile(r"\b(?:for|while)\s*\(")),
    ("exception_boundary", re.compile(r"\btry\s*\{|\bcatch\s*\(")),
    ("throws", re.compile(r"\bthrow\s+new\b|\bthrows\b")),
    ("early_return", re.compile(r"\breturn\b")),
)

_EFFECT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("reads_context", re.compile(r"\bcontext\.[A-Za-z_]")),
    ("reads_shared_state", re.compile(r"\bglobalMap\s*\.\s*get\s*\(")),
    ("writes_shared_state", re.compile(r"\bglobalMap\s*\.\s*put\s*\(")),
    (
        "emits_log_output",
        re.compile(r"\b(?:log\.(?:trace|debug|info|warn|error)|System\.out\.print)\s*\("),
    ),
    ("terminates_process", re.compile(r"\bSystem\.exit\s*\(")),
)


@dataclass
class WithholdingCensus:
    """How much verbatim code the projection removed, for disclosure.

    Counts, never content. ``facts/parser_limitations.md`` renders this so a
    document can disclose the boundary with a population attached instead of
    hedging, and so an operator reading the receipt can tell a code-heavy
    estate from one where the removal changed nothing.
    """

    bodies: int = 0
    lines: int = 0
    characters: int = 0
    #: Staged JSON files at least one body was removed from.
    artifacts: int = 0


def project_scan_payload(payload: Any, *, census: WithholdingCensus | None = None) -> Any:
    """Return ``payload`` with every verbatim code body replaced by a summary.

    Deterministic and byte-stable: key order follows the source document,
    every derived list is sorted, and nothing iterates a set.
    """

    census = census if census is not None else WithholdingCensus()
    before = census.bodies
    projected = _project(payload, census)
    if census.bodies > before:
        census.artifacts += 1
    return projected


def verbatim_code_findings(payload: Any, *, label: str) -> list[str]:
    """Every location in ``payload`` still holding a non-empty code body.

    The assertion half of the boundary, so a future edit that reintroduces a
    plain copy fails the build instead of shipping. Findings name the JSON
    path and never the value: this string reaches a log and an exception
    message, and a body is exactly what must not be copied into either.
    """

    findings: list[str] = []
    _find(payload, label, findings)
    return sorted(dict.fromkeys(findings))


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _project(node: Any, census: WithholdingCensus) -> Any:
    if isinstance(node, list):
        return [_project(item, census) for item in node]
    if not isinstance(node, dict):
        return node

    bodies: list[tuple[str, str]] = []
    projected: dict[str, Any] = {}
    for key, value in node.items():
        if key in VERBATIM_CODE_FIELDS:
            if isinstance(value, str):
                bodies.append((key, value))
            # The key is dropped whether or not it held anything. Leaving
            # `code: ""` beside a `code_withheld` block invites the reader to
            # conclude the component has no custom code, which is the
            # inference this module exists to stop.
            continue
        if key == "params" and isinstance(value, list):
            kept, code_params = _split_code_params(value)
            bodies.extend(code_params)
            projected[key] = [_project(item, census) for item in kept]
            continue
        projected[key] = _project(value, census)

    summary = _withheld_summary(bodies, census)
    if summary is not None:
        projected[WITHHELD_KEY] = summary
    return projected


def _split_code_params(
    params: list[Any],
) -> tuple[list[Any], list[tuple[str, str]]]:
    """Split ``params`` into settings and code-bearing entries.

    A parameter named in :data:`contract.CODE_PARAM_NAMES` holds a code body
    as its VALUE; everything else is a setting and stays. The match is on the
    name alone, so a non-Talend artifact that happens to carry a parameter
    literally called ``CODE`` loses it -- the safe direction, and the loss is
    recorded in the summary rather than being silent.
    """

    kept: list[Any] = []
    bodies: list[tuple[str, str]] = []
    for item in params:
        name = str(item.get("name") or "") if isinstance(item, dict) else ""
        if name in CODE_PARAM_NAMES:
            value = item.get("value")
            bodies.append((f"params.{name}", value if isinstance(value, str) else ""))
            continue
        kept.append(item)
    return kept, bodies


def _withheld_summary(
    bodies: list[tuple[str, str]], census: WithholdingCensus
) -> dict[str, Any] | None:
    """The stated replacement for one owner's removed code, or ``None``.

    An empty code slot carries no information, so it produces no block: a
    summary reading "0 characters withheld" would be noise on every tJava the
    designer left blank, and noise is how a real disclosure stops being read.
    """

    texts = [text for _, text in bodies if text.strip()]
    if not texts:
        return None
    joined = "\n".join(texts)
    census.bodies += 1
    census.lines += len(joined.splitlines())
    census.characters += len(joined)
    return {
        "withheld": True,
        "reason": WITHHELD_REASON,
        "sources": [label for label, text in bodies if text.strip()],
        "lines": len(joined.splitlines()),
        "characters": len(joined),
        "sha256": hashlib.sha256(joined.encode("utf-8")).hexdigest(),
        "references": _references(joined),
        "behavior_summary": _behavior_summary(joined),
    }


def _behavior_summary(text: str) -> dict[str, Any]:
    """Return safe, non-verbatim behavioral facts about a removed body.

    This is intentionally not a natural-language summary and does not attempt
    to reconstruct the program.  It tells an investigator which behavioral
    questions the hidden body contains (branching, state mutation, exception
    handling, logging) without carrying any source expression or literal into
    the agent workspace.
    """

    control_flow = [label for label, pattern in _CONTROL_FLOW_PATTERNS if pattern.search(text)]
    effects = [label for label, pattern in _EFFECT_PATTERNS if pattern.search(text)]
    return {
        "control_flow": control_flow,
        "effects": effects,
        "routine_call_count": len(_ROUTINE_RE.findall(text)),
        "context_reference_count": len(_CONTEXT_RE.findall(text)),
        "shared_state_reference_count": len(_GLOBAL_MAP_RE.findall(text)),
        "detail_status": "structural_only",
        "detail_limit": (
            "the source body was withheld; labels show observed constructs, "
            "not their conditions, values, or business meaning"
        ),
    }


def _references(text: str) -> dict[str, Any]:
    """Structured identifiers the removed body named. Facts, not source text.

    Routine names, context variables and ``globalMap`` keys are already
    present elsewhere in the approved scan -- the context groups list their
    own variable names, the manifest lists routine links -- so publishing them
    here exposes nothing new and tells a document what the code touches. Every
    capture is re-validated against :data:`_IDENTIFIER_RE`, which is what
    keeps a ``globalMap`` key from smuggling an arbitrary literal out.
    """

    references: dict[str, Any] = {}
    truncated = False
    for kind, pattern in _REFERENCE_KINDS:
        found = sorted({match for match in pattern.findall(text) if _IDENTIFIER_RE.match(match)})
        if len(found) > _MAX_REFERENCES:
            found = found[:_MAX_REFERENCES]
            truncated = True
        references[kind] = found
    if truncated:
        references["truncated"] = True
    return references


def _find(node: Any, path: str, findings: list[str], *, in_params: bool = False) -> None:
    """Mirror :func:`_project` exactly, including where it does NOT strip.

    ``in_params`` is the load-bearing part. The name match is only a code
    body inside a component's ``params`` list; an ``<xsl:variable name="CODE">``
    in ``scan/xslt.json`` is an ordinary XSLT variable with a plausible name,
    and this check FAILS THE BUILD -- so a name match that ignores position
    would refuse to stage a perfectly safe estate with no way for an operator
    to tell why.
    """

    if isinstance(node, list):
        for index, item in enumerate(node):
            _find(item, f"{path}[{index}]", findings, in_params=in_params)
        return
    if not isinstance(node, dict):
        return
    if (
        in_params
        and str(node.get("name") or "") in CODE_PARAM_NAMES
        and isinstance(node.get("value"), str)
        and node["value"].strip()
    ):
        findings.append(f"{path}.{node['name']}")
    for key, value in node.items():
        if key in VERBATIM_CODE_FIELDS and isinstance(value, str) and value.strip():
            findings.append(f"{path}.{key}")
            continue
        _find(value, f"{path}.{key}", findings, in_params=key == "params")
