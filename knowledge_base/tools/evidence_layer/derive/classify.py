"""Classify a transformation as business, technical or workaround.

Signals live in ``vocab/classification.yaml``. Every verdict carries the signal
ids that produced it, so a reviewer argues with a named rule instead of with an
opaque label -- and a rule that turns out to be wrong is a YAML edit.

This is what makes "which artifacts contain business-level mappings?"
answerable across a whole landscape rather than one chain at a time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..models import RuleClass

_VOCAB_DIR = Path(__file__).resolve().parent.parent / "vocab"

# Marker the assembler injects when it detects two target fields fed by one
# source with no distinguishing transform. Structural, not textual, so it
# cannot be expressed as a regex over a single expression.
DUPLICATE_TARGET_MARKER = "__DUPLICATE_TARGET__"


@dataclass(frozen=True)
class _Signal:
    class_name: str
    signal_id: str
    pattern: re.Pattern[str]
    weight: int
    rationale: str


@dataclass(frozen=True)
class Verdict:
    rule_class: str
    signals: tuple[str, ...]
    rationales: tuple[str, ...]

    @property
    def is_business(self) -> bool:
        return self.rule_class == RuleClass.BUSINESS


class Classifier:
    def __init__(self, signals: list[_Signal], precedence: dict[str, int]) -> None:
        self._signals = signals
        self._precedence = precedence

    @classmethod
    def load(cls, *extra: Path | str) -> "Classifier":
        signals: list[_Signal] = []
        precedence: dict[str, int] = {}
        for path in [_VOCAB_DIR / "classification.yaml", *extra]:
            if path is None:
                continue
            p = Path(path)
            if not p.is_file():
                if p == _VOCAB_DIR / "classification.yaml":
                    raise FileNotFoundError(f"shipped signals missing: {p}")
                continue
            doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            for class_name, spec in (doc.get("classes") or {}).items():
                precedence[class_name] = int(spec.get("precedence") or 0)
                for s in spec.get("signals") or []:
                    pat = s.get("pattern")
                    if not pat:
                        continue
                    try:
                        compiled = re.compile(pat, re.IGNORECASE | re.DOTALL)
                    except re.error as exc:  # a bad rule must fail loudly
                        raise ValueError(
                            f"bad pattern for {class_name}.{s.get('id')}: {exc}"
                        ) from exc
                    signals.append(_Signal(
                        class_name=class_name,
                        signal_id=str(s.get("id") or "unnamed"),
                        pattern=compiled,
                        weight=int(s.get("weight") or 1),
                        rationale=str(s.get("rationale") or ""),
                    ))
        return cls(signals, precedence)

    def classify(self, expression: str) -> Verdict:
        """Score every signal, then let weight decide and precedence break ties."""
        if not expression or not expression.strip():
            return Verdict(RuleClass.NONE, (), ())

        scores: dict[str, int] = {}
        hits: dict[str, list[_Signal]] = {}
        for sig in self._signals:
            if sig.pattern.search(expression):
                scores[sig.class_name] = scores.get(sig.class_name, 0) + sig.weight
                hits.setdefault(sig.class_name, []).append(sig)

        if not scores:
            return Verdict(RuleClass.NONE, (), ())

        # highest weight wins; equal weight -> declared precedence
        best = max(
            scores.items(),
            key=lambda kv: (kv[1], self._precedence.get(kv[0], 0)),
        )[0]
        chosen = sorted(hits[best], key=lambda s: (-s.weight, s.signal_id))
        return Verdict(
            rule_class=best,
            signals=tuple(f"{s.class_name}:{s.signal_id}" for s in chosen),
            rationales=tuple(s.rationale for s in chosen if s.rationale),
        )

    def signal_ids(self) -> tuple[str, ...]:
        return tuple(sorted(f"{s.class_name}:{s.signal_id}" for s in self._signals))
