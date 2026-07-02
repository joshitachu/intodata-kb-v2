"""G5 -- validate-against-diff guard (Living-Document Reingestion, Slice 2).

Before a Phase 2 patch's model-returned section content is merged into the
group's analysis (and long before anything reaches disk), verify that the
mechanically-checkable facts it asserts -- unit/job names, component (field,
connection) names, parameter/config-key names, and their new values -- are
reconcilable with the group's actual field diff (the ``FieldChange`` list
already computed by ``_compute_field_diff`` in ``true_patch_executor``).

A fact asserted by the rewritten section text is "reconciled" when it is
either:

  - part of the diff vocabulary (a job/component/param name, or an
    old/new value the diff actually introduced for this group), or
  - present in the section's PRE-EXISTING content (a stable, unchanged
    carryover fact the amendment is legitimately allowed to keep restating).

A fact that is neither is treated as an unfounded assertion: the model
referenced a name that appears nowhere in the group's diff and was not
already part of the document -- i.e. it is not mechanically traceable to
any evidence this patch run actually has. This is a cheap, precision-biased
lexical check, not a semantic one; it is a safety net against hallucinated
identifiers, not a correctness prover.

Pure module: no I/O, no config, no model calls -- unit-testable standalone.
Reused by ``TruePatchExecutor._patch_phase2`` (see G5 in that module's
guardrail docstring).
"""

from __future__ import annotations

import re

from mcp_server.services.patch_section_map import FieldChange

# Backtick-quoted identifiers/values, e.g. `tMap_1`, `HR_DB`, `MAPPING`.
_BACKTICK_RE = re.compile(r"`([^`\n]{2,80})`")

# Component-style identifiers, e.g. tMap_1, tFileInputDelimited_2, Job_Payroll_3.
_COMPONENT_TOKEN_RE = re.compile(r"\b[a-zA-Z][A-Za-z0-9]*_[A-Za-z0-9_]*[A-Za-z0-9]\b")

# ALL_CAPS parameter/config-key style tokens, e.g. MAPPING_KEY, DB_HOST.
_ALLCAPS_TOKEN_RE = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")

# BR-{GROUP}-NNN evidence citation tags (see true_patch_executor._br_tags). These
# embed the group name (e.g. "BR-GROUP_A-001" -> "GROUP_A") but are citations
# governed by the G2 invariant guard elsewhere, not mechanical facts an amendment
# "asserts". Stripped before extraction so a legitimately new citation number
# doesn't leak its group-name fragment into the G5 fact set and cause a false
# rejection.
_BR_TAG_RE = re.compile(r"\bBR-[A-Za-z0-9_-]+-\d+\b")


def extract_mechanical_facts(text: str) -> set[str]:
    """Return the set of candidate mechanically-checkable identifiers in ``text``.

    Extraction is intentionally narrow (backtick spans, ``component_N``-style
    tokens, ``ALL_CAPS_WITH_UNDERSCORES`` tokens) -- a precision-biased
    heuristic for names an amendment can mechanically assert, not a general
    NLP extractor. Missed facts just leave the guard silent on that fact;
    they never cause a false rejection of ordinary prose.
    """
    if not text:
        return set()
    text = _BR_TAG_RE.sub(" ", text)
    facts: set[str] = set()
    facts.update(m.strip() for m in _BACKTICK_RE.findall(text))
    facts.update(_COMPONENT_TOKEN_RE.findall(text))
    facts.update(_ALLCAPS_TOKEN_RE.findall(text))
    return {f for f in facts if f}


def diff_vocabulary(field_changes: list[FieldChange]) -> set[str]:
    """Return the set of names/values legitimately introduced by ``field_changes``.

    Parses ``FieldChange.field_path`` (``"(job_name, unique_name, param_name)"``)
    plus each change's ``old_value``/``new_value`` into a flat vocabulary of
    grounded, diff-backed facts. Values are added both whole (e.g. a full
    connection string) and as their extracted mechanical sub-tokens (e.g. the
    ``NEW_OUTPUT`` identifier embedded in ``"emp -> NEW_OUTPUT"``), since a
    legitimate amendment may restate either form.
    """
    vocab: set[str] = set()
    for fc in field_changes:
        inner = fc.field_path.strip("()")
        parts = [p.strip() for p in inner.split(",")]
        vocab.update(p for p in parts if p)
        for value in (fc.old_value, fc.new_value):
            if not value:
                continue
            value = value.strip()
            vocab.add(value)
            vocab.update(extract_mechanical_facts(value))
    return {v for v in vocab if v}


def find_unreconciled_facts(
    *,
    rewritten_text: str,
    original_text: str,
    field_changes: list[FieldChange],
) -> set[str]:
    """Return facts asserted in ``rewritten_text`` that cannot be reconciled.

    A fact is unreconciled when it is absent from both the group's diff
    vocabulary (``field_changes``) and the section's pre-existing content
    (``original_text``). Empty result means the amendment passes G5.
    """
    asserted = extract_mechanical_facts(rewritten_text)
    if not asserted:
        return set()
    vocab = diff_vocabulary(field_changes)
    existing = extract_mechanical_facts(original_text)
    return {f for f in asserted if f not in vocab and f not in existing}
