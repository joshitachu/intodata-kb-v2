"""Deterministic production/test tiering for Grounded per-unit documentation.

Slice 4 fans a per-unit "Tier-A" deep document out over the whole project. Not
every scanned unit deserves the same depth: real integration jobs must be
documented exhaustively, but throwaway test/sandbox/scratch units only warrant a
light summary. This module classifies each canonical unit as ``production`` or
``test`` from GENERAL, provider-neutral signals -- never a single hard-coded name
list -- and always errs toward ``production`` when the signals are ambiguous, so a
real job is never silently downgraded (and, downstream, never dropped from the
coverage gate).

Signals (combined, not any one on its own):
- **name/group token** (strong) -- a word part of the unit name or its group is a
  well-known non-production token (``test``, ``sandbox``, ``playground``,
  ``scratch``, ``voorbeeld``, ``mock``, ``demo``, ``poc``, ``wip`` ...). Matching is
  on tokenised word parts (CamelCase / snake / digit boundaries), so ``latest`` or
  ``contest`` never trip the ``test`` token.
- **orphan** (weak) -- the unit neither calls another unit nor is called by one.
- **zero components** (weak) -- an empty stub with nothing implemented.
- **0.x version** (weak) -- an unreleased draft version (``0.1`` etc.).

Decision: a unit is ``test`` only when it has the strong token signal OR at least
two weak signals; a single weak signal on its own leaves it ``production``. The
``force_all_deep`` flag (config ``MCP_GROUNDED_FORCE_ALL_UNITS_DEEP``) overrides
everything and marks every unit ``production``. The per-unit tier and the exact
reasons are emitted in a transparent manifest.

Standalone by design: primitive ledger mapping in, decisions out. Holds no
reference to the staging layout, the runtime, or ``providers.custom``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping, Sequence

PRODUCTION = "production"
TEST = "test"

# Word parts (not substrings) that mark a unit as non-production scaffolding.
# This is one signal among several -- see the module docstring.
_NONPROD_TOKENS = frozenset(
    {
        "test",
        "tests",
        "testing",
        "sandbox",
        "playground",
        "scratch",
        "voorbeeld",  # Dutch: "example"
        "voorbeelden",
        "tbd",
        "mock",
        "mocks",
        "demo",
        "demos",
        "sample",
        "samples",
        "poc",
        "prototype",
        "dummy",
        "example",
        "examples",
        "experiment",
        "experimental",
        "wip",
        "temp",
        "tmp",
        "throwaway",
        "trial",
        "spike",
        "junk",
    }
)

# Split on any non-alphanumeric run and on camelCase / letter<->digit boundaries.
_WORD_SPLIT_RE = re.compile(
    r"[^0-9A-Za-z]+"
    r"|(?<=[a-z0-9])(?=[A-Z])"
    r"|(?<=[A-Za-z])(?=[0-9])"
    r"|(?<=[0-9])(?=[A-Za-z])"
)
_ZERO_X_VERSION_RE = re.compile(r"^0(?:\.0*[0-9]+.*)?$")


@dataclass(frozen=True, slots=True)
class UnitTierDecision:
    """One unit's production/test classification with its transparent reasons."""

    unit: str
    group: str
    tier: str
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_production(self) -> bool:
        return self.tier == PRODUCTION


def _word_parts(text: str) -> set[str]:
    return {part.lower() for part in _WORD_SPLIT_RE.split(text or "") if part}


def _nonprod_token_hits(*texts: str) -> list[str]:
    hits: list[str] = []
    for text in texts:
        for part in sorted(_word_parts(text) & _NONPROD_TOKENS):
            if part not in hits:
                hits.append(part)
    return hits


def _call_relations(ledger: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    """Return ``(has_callees, is_callee)`` unit-name sets from the call graph."""

    has_callees: set[str] = set()
    is_callee: set[str] = set()
    for edge in ledger.get("call_graph", []) or []:
        if not isinstance(edge, dict):
            continue
        caller = str(edge.get("caller") or "").strip()
        callees = [str(c) for c in edge.get("callees", []) or [] if isinstance(c, str) and c.strip()]
        if caller and callees:
            has_callees.add(caller)
        for callee in callees:
            is_callee.add(callee)
    return has_callees, is_callee


def classify_units(
    ledger: Mapping[str, Any],
    *,
    force_all_deep: bool = False,
) -> tuple[UnitTierDecision, ...]:
    """Classify every canonical unit in ``ledger`` as production or test.

    Deterministic and order-stable (ledger canonical-unit order). Never drops a
    unit; ambiguous units default to ``production``.
    """

    has_callees, is_callee = _call_relations(ledger)
    decisions: list[UnitTierDecision] = []
    for item in ledger.get("canonical_units", []) or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        group = str(item.get("group") or "ungrouped")

        if force_all_deep:
            decisions.append(
                UnitTierDecision(
                    unit=name,
                    group=group,
                    tier=PRODUCTION,
                    reasons=("forced all-units-deep (MCP_GROUNDED_FORCE_ALL_UNITS_DEEP)",),
                )
            )
            continue

        reasons: list[str] = []
        token_hits = _nonprod_token_hits(name, group)
        strong = bool(token_hits)
        if strong:
            reasons.append(
                "name/group non-production token(s): " + ", ".join(token_hits)
            )

        weak: list[str] = []
        if name not in has_callees and name not in is_callee:
            weak.append("orphan: no callers and no callees in the call graph")
        component_count = item.get("component_count")
        if isinstance(component_count, int) and not isinstance(component_count, bool) and component_count == 0:
            weak.append("zero components (empty stub)")
        version = str(item.get("version") or "").strip()
        if version and version.lower() != "unknown" and _ZERO_X_VERSION_RE.match(version):
            weak.append(f"unreleased 0.x version ({version})")
        reasons.extend(weak)

        if strong or len(weak) >= 2:
            tier = TEST
        else:
            tier = PRODUCTION
            if weak:
                reasons.append(
                    "single weak signal only; defaulting to production (err toward production)"
                )
            else:
                reasons.append("no non-production signal; production")
        decisions.append(
            UnitTierDecision(unit=name, group=group, tier=tier, reasons=tuple(reasons))
        )
    return tuple(decisions)


def production_unit_names(decisions: Sequence[UnitTierDecision]) -> tuple[str, ...]:
    return tuple(decision.unit for decision in decisions if decision.is_production)


def decisions_by_unit(
    decisions: Sequence[UnitTierDecision],
) -> dict[str, UnitTierDecision]:
    return {decision.unit: decision for decision in decisions}


def tiering_manifest(
    decisions: Sequence[UnitTierDecision],
    *,
    force_all_deep: bool,
) -> dict[str, Any]:
    """Transparent per-unit tier manifest with the reasons behind each split."""

    production = [d for d in decisions if d.is_production]
    test = [d for d in decisions if not d.is_production]
    return {
        "schema_version": 1,
        "description": (
            "Production/test tiering for per-unit (Tier-A) documentation. Production "
            "units receive an exhaustive deep document grounded in their verbatim "
            "evidence bundle; test/scratch units receive a lighter summary. "
            "Classification errs toward production when signals are ambiguous, and "
            "MCP_GROUNDED_FORCE_ALL_UNITS_DEEP forces every unit to production."
        ),
        "force_all_units_deep": force_all_deep,
        "unit_count": len(decisions),
        "production_count": len(production),
        "test_count": len(test),
        "units": [
            {
                "unit": decision.unit,
                "group": decision.group,
                "tier": decision.tier,
                "reasons": list(decision.reasons),
            }
            for decision in decisions
        ],
    }
