"""Assign a semantic category to a field from a declarative vocabulary.

The vocabulary lives in ``vocab/categories.yaml``, not here. That is the point:
the set of questions the layer can answer by category is a data concern. Adding
"which interfaces carry person data?" or a client-specific category is an edit
to a YAML file, not a code change.

Never returns "no category": an unmatched field is ``uncategorised`` with
``unresolved`` confidence, so a consumer can always distinguish "we looked and
found nothing" from "we never looked".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..models import Confidence, UNCATEGORISED

_VOCAB_DIR = Path(__file__).resolve().parent.parent / "vocab"


@dataclass(frozen=True)
class _Category:
    name: str
    description: str
    order: int
    exact: frozenset[str]
    name_re: re.Pattern[str] | None
    source_re: re.Pattern[str] | None
    value_re: re.Pattern[str] | None
    severity: str


def _compile(pattern: str | None) -> re.Pattern[str] | None:
    if not pattern:
        return None
    return re.compile(pattern, re.IGNORECASE)


class Categoriser:
    """Field name (+ optional source expression / observed values) -> category."""

    def __init__(self, categories: list[_Category]) -> None:
        self._categories = categories

    # -- construction ----------------------------------------------------

    @classmethod
    def load(cls, *extra: Path | str) -> "Categoriser":
        """Load the shipped vocabulary, then merge any overlays by name.

        An overlay entry with an existing name replaces that category outright
        (so a client can retune a pattern); a new name is appended after the
        shipped ones, which keeps shipped precedence stable.
        """
        merged: dict[str, dict[str, Any]] = {}
        order: list[str] = []

        for path in [_VOCAB_DIR / "categories.yaml", *extra]:
            if path is None:
                continue
            p = Path(path)
            if not p.is_file():
                if p == _VOCAB_DIR / "categories.yaml":
                    raise FileNotFoundError(f"shipped vocabulary missing: {p}")
                continue
            doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            for entry in doc.get("categories") or []:
                name = entry.get("name")
                if not name:
                    continue
                if name not in merged:
                    order.append(name)
                merged[name] = entry

        cats: list[_Category] = []
        for i, name in enumerate(order):
            e = merged[name]
            cats.append(_Category(
                name=name,
                description=str(e.get("description") or ""),
                order=i,
                exact=frozenset(str(x).casefold() for x in (e.get("exact") or [])),
                # NOT `name`: that key is the category's own identity. A
                # single YAML key serving as both label and pattern silently
                # loses the label to the last-writer-wins merge.
                name_re=_compile(e.get("name_pattern")),
                source_re=_compile(e.get("source")),
                value_re=_compile(e.get("value")),
                severity=str(e.get("severity") or ""),
            ))
        return cls(cats)

    # -- classification --------------------------------------------------

    def categorise(
        self,
        field_path: str,
        source_expr: str = "",
        values: tuple[str, ...] = (),
    ) -> tuple[str, str, tuple[str, ...]]:
        """Return ``(category, confidence, signals)``.

        Tiers are tried highest-confidence first across ALL categories before
        dropping to the next tier -- otherwise an early category's weak
        source-pattern match would beat a later category's exact name match,
        which is the wrong answer.
        """
        leaf = field_path.rsplit("/", 1)[-1].strip()
        leaf_cf = leaf.casefold()
        haystacks = (leaf, field_path)

        # tier 1: exact leaf name
        for cat in self._categories:
            if leaf_cf in cat.exact:
                return cat.name, Confidence.CERTAIN, (f"exact:{cat.name}",)

        # tier 2: name regex against leaf or full path
        for cat in self._categories:
            if cat.name_re and any(cat.name_re.search(h) for h in haystacks):
                return cat.name, Confidence.LIKELY, (f"name:{cat.name}",)

        # tier 3: source expression
        if source_expr:
            for cat in self._categories:
                if cat.source_re and cat.source_re.search(source_expr):
                    return cat.name, Confidence.INFERRED, (f"source:{cat.name}",)

        # tier 4: observed values
        if values:
            joined = "\n".join(values[:50])
            for cat in self._categories:
                if cat.value_re and cat.value_re.search(joined):
                    return cat.name, Confidence.INFERRED, (f"value:{cat.name}",)

        return UNCATEGORISED, Confidence.UNRESOLVED, ()

    # -- introspection ---------------------------------------------------

    def names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self._categories)

    def severity(self, category: str) -> str:
        for c in self._categories:
            if c.name == category:
                return c.severity
        return ""

    def describe(self) -> list[dict[str, str]]:
        return [{"name": c.name, "description": c.description,
                 "severity": c.severity} for c in self._categories]
