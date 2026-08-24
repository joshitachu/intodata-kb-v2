#!/usr/bin/env python3
"""Express the BUILT-IN documentation behaviour as a pack, so it can be read.

The built-in behaviour is spread across four places in three shapes: the
document set in ``data/doc_catalog.yaml``, the section list in Python constants,
the prompt spine in ``data/prompt_templates/phase2_analysis.md``, and what each
section actually means in ``phase2_platform_sections/<platform>.md``. A pack
author who wants "the standard documents, minus one, plus one of my own" has
nothing to start from and no single file to read.

This emits that behaviour as a pack in the ONE format -- something you can diff
against your own pack, and fork.

It is deliberately a GENERATOR, not a hand-written file. A second hand-written
copy of the default would drift the first time someone edited the catalog, and a
reference that lies is worse than no reference. Here the content is derived from
the live catalog and the live contract, and ``test_reference_pack.py`` fails if
the committed output stops matching what this produces.

The output is INERT. Nothing loads it at runtime -- no default-pack activation,
no registration on boot. It is documentation you can execute if you choose to
register it, and nothing more.

    python scripts/generate_reference_pack.py [--check]

``--check`` writes nothing and exits non-zero if the committed pack is stale.
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

REPO_MCP_SERVER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_MCP_SERVER / "src"))

from mcp_server.providers.custom.doc_catalog import load_doc_catalog  # noqa: E402
from mcp_server.providers.custom.pack_contract import default_contract  # noqa: E402
from mcp_server.providers.custom.prompts import (  # noqa: E402
    _PHASE2_PREAMBLE_DEFAULT,
    _PHASE3_PREAMBLE_DEFAULT,
    _SCORECARD_PREAMBLE_DEFAULT,
)
from mcp_server.services.pack_prompt_slots import PHASE_TEMPLATES  # noqa: E402

#: Where the generated reference pack lands. `pack-repos/` is a staging area for
#: pack repository content, NOT a runtime path -- keeping it out of
#: `mcp-server/data/` is deliberate, so it can never be mistaken for something
#: the server loads.
OUTPUT_DIR = REPO_MCP_SERVER.parent / "pack-repos" / "nexus-baseline"

#: The reference is generated for Talend because that is the platform whose
#: built-in contract is strictest (16 sections, 13 hard-required) and whose
#: section skeleton is fully written. Other platforms enforce the GROUP_SUMMARY
#: floor alone, so a Talend reference is the superset worth reading.
PLATFORM = "talend"

def _quote(text: str) -> str:
    """Render as a YAML block scalar, indented for a list-item field."""
    body = "\n".join(f"          {line}".rstrip() for line in text.strip().splitlines())
    return f">\n{body}"


def _manifest() -> str:
    catalog = load_doc_catalog()
    contract = default_contract(PLATFORM)
    sections = contract.analysis_section_names

    lines = [
        "# nexus-baseline -- the standard Nexus documentation set, in pack form.",
        "#",
        "# GENERATED -- do not hand-edit. Regenerate with:",
        "#     python scripts/generate_reference_pack.py",
        "# A test fails if this drifts from the live catalog and contract.",
        "#",
        "# This is what a run does when NO pack is selected. It is here to be read",
        "# and forked, not to be the default -- the default is the code.",
        "#",
        "# To make your own: register this branch, or copy it, then delete the",
        "# documents you do not want and trim `lens.sections.declare` to match.",
        "# Every section you remove is analysis budget you stop spending.",
        "",
        "name: nexus-baseline",
        "version: 1.0.0",
        "",
        "# Required for a branch of the pack repository to be registerable: the",
        "# governance config matches this against the trusted source's provenance,",
        "# and discovery skips any branch whose manifest does not declare it.",
        "certification:",
        f"  provenance: {_provenance()}",
        "description: >",
        "  The standard Nexus documentation set -- what a run produces when no pack",
        "  is selected -- expressed as a pack so it can be read and forked.",
        "",
        "# Every phase runs. This is the most expensive configuration there is --",
        "# a focused pack almost always wants investigation: skip and",
        "# analysis: bounded instead.",
        "phases:",
        "  investigation: run",
        "  analysis: run",
        "  scorecard: skip          # MCP_DOCGEN_LLM_SCORECARD_ENABLED, default off",
        "  doc_bundles: run",
        "",
        "lens:",
        "  sections:",
        "    # `extend` and the built-in list are the same thing, so this block is a",
        "    # no-op by construction -- which is the point: it shows you the contract.",
        "    # Switch to `replace` and delete lines to narrow the analysis.",
        "    mode: extend",
        "    declare:",
    ]

    # Legacy declares section NAMES; what each one means lives in the platform
    # skeleton, which becomes prompts/phase2.md below. So `question` is left
    # empty here rather than invented -- writing a question the built-in path
    # never asks would make this reference wrong in a way that reads as right.
    for name in sections:
        lines.append(f"      - id: {name}")

    lines += [
        "",
        "  # `lens.requirements` is deliberately ABSENT. It replaces the platform",
        "  # evidence contract, and that contract is per-platform -- one string",
        "  # here would apply Talend's requirements to a Workato run. Leaving it",
        "  # out keeps the real per-platform text, which is what this pack is",
        "  # mirroring. Set it only when you want to override all platforms.",
    ]

    lines += [
        "",
        f"# {len(catalog.docs)} documents, from data/doc_catalog.yaml. Each document's",
        "# `phase2_sections` is the analysis routed into its evidence bundle.",
        "docs:",
    ]
    for doc in catalog.docs:
        lines.append(f"  - name: {doc.name}")
        if doc.per_unit:
            lines.append("    per_unit: true        # one document per scanned unit")
        if doc.phase2_sections:
            lines.append("    phase2_sections:")
            lines += [f"      - {s}" for s in doc.phase2_sections]
        if doc.required_sections:
            lines.append("    required_sections:")
            lines += [f"      - {s}" for s in doc.required_sections]
        lines.append("")

    lines += [
        "# The built-in path applies no audience directives -- presentation comes",
        "# from the request (the Style layer) or from the document templates.",
        "# Uncomment to pin it in the pack instead:",
        "#",
        "# audience:",
        "#   roles: [Integration engineer]",
        "#   tone: neutral",
        "#   technical_depth: deep",
        "#   output_organization: sectioned",
    ]
    return "\n".join(lines) + "\n"


#: Every platform with a section skeleton. A fork on Workato must not silently
#: inherit only Talend guidance.
_PLATFORMS = ("talend", "workato", "lobster", "generic")


def _provenance() -> str:
    """The provenance the pack repository is configured to certify under.

    Read from the live governance config rather than hardcoded: if the trusted
    source is renamed, a hardcoded value would produce a pack that silently
    fails branch discovery.
    """
    import json

    from mcp_server.config import config

    try:
        for entry in json.loads(config.pack_governance_trusted_sources_json or "[]"):
            if entry.get("kind") == "git" and entry.get("provenance"):
                return str(entry["provenance"])
    except (json.JSONDecodeError, AttributeError, TypeError):
        pass
    return "swap-pack-starter"


#: The overridable prompt surface is defined ONCE, in
#: ``services/pack_prompt_slots.py``, and imported here. Two lists would
#: disagree the first time a phase gained a prompt, and the copy that fell
#: behind would be this one -- the pack that actually ships to authors.
_PHASE_TEMPLATES = tuple(
    (phase, tuple(name for name, _description in entries))
    for phase, entries in PHASE_TEMPLATES
)


def _prompt_files() -> dict[str, str]:
    """Every prompt a pack can replace, each carrying its real default.

    Two namespaces, and the difference is not cosmetic:

    * ``prompts/*.md`` are PREAMBLES -- text substituted into a ``${PREAMBLE}``
      slot inside a template. An earlier version of this generator put the
      section SKELETON into ``prompts/phase2.md``, and the result was the
      skeleton rendered three times with no preamble at all: a pack that looked
      complete and produced a broken prompt.
    * ``prompts/templates/*.md`` are the BODIES -- the whole prompt, including
      the parts that carry most of the instruction (the unit-enumeration
      obligation, the output-size contract, the redaction and fabrication
      rules). Until these were overridable, a pack could not express them, so
      any pack claiming to hold "the documentation behaviour" was omitting the
      majority of it.
    """
    files: dict[str, str] = {
        "prompts/phase2.md": _PHASE2_PREAMBLE_DEFAULT.rstrip() + "\n",
        "prompts/phase3.md": _PHASE3_PREAMBLE_DEFAULT.rstrip() + "\n",
        "prompts/scorecard.md": _SCORECARD_PREAMBLE_DEFAULT.rstrip() + "\n",
    }

    template_root = REPO_MCP_SERVER / "data" / "prompt_templates"
    for _phase, template_names in _PHASE_TEMPLATES:
        for name in template_names:
            source = template_root / name
            if not source.is_file():
                raise SystemExit(f"listed template does not exist: {source}")
            files[f"prompts/templates/{name}"] = source.read_text(encoding="utf-8")

    # Nexus owns the platform section skeletons. Ship byte-identical copies for
    # transparency, but keep them outside prompts/: a plugin cannot replace
    # platform semantics. Additions belong in
    # prompts/extensions/platforms/<platform>.md.
    for platform in _PLATFORMS:
        source = (
            REPO_MCP_SERVER / "data" / "prompt_templates"
            / "phase2_platform_sections" / f"{platform}.md"
        )
        if not source.is_file():
            continue
        files[f"reference/immutable-platform-analysis/{platform}.md"] = (
            source.read_text(encoding="utf-8")
        )

    # Per-document instructions. In the built-in path these live in
    # doc_catalog.yaml as `doc_specific_instructions`; a pack supplies them as
    # prompts/docs/<DOCUMENT>.md, which fully replaces the built-in text.
    for doc in load_doc_catalog().docs:
        instructions = (doc.doc_specific_instructions or "").rstrip()
        if not instructions:
            continue
        files[f"prompts/docs/{doc.name}.md"] = instructions + "\n"
    return files


#: Phase order as ``PipelineRunner`` executes it, with what each phase costs in
#: model calls. The deterministic phases are listed too: "no prompt" is the
#: answer to "which prompt runs here", and omitting them would read as an
#: oversight.
_PHASE_WALKTHROUGH: tuple[tuple[str, str, str], ...] = (
    ("phase1_investigation", "LLM",
     "Reads scanner chunks and extracts per-unit evidence. In manifest mode it "
     "first asks which chunks are worth reading, then extracts from those only."),
    ("phase2_analysis", "LLM",
     "The core pass. One call per bin-packed evidence task: answers the declared "
     "sections for the units whose evidence is in that task, plus a claim-"
     "extraction call producing the bounded claim index."),
    ("phase26_scorecard", "LLM",
     "Advisory quality/security scorecard. OFF by default "
     "(MCP_DOCGEN_LLM_SCORECARD_ENABLED)."),
    ("phase28_bundles", "no prompt",
     "Deterministic Python. Routes accepted analysis into a per-document "
     "evidence bundle using each document's phase2_sections."),
    ("evidence_quality_gate", "no prompt",
     "Deterministic Python. Flags groups whose evidence bundle is too thin to "
     "document honestly."),
    ("phase3_docs", "LLM",
     "One call per document (and per unit for per_unit documents). Writes the "
     "markdown from the bundle it was given."),
    ("phase37_business_topology", "LLM",
     "One synthesis call emitting the business-topology JSON graph from scanner "
     "facts plus accepted Phase 1/2 evidence."),
    ("phase4_cleanup", "no prompt",
     "Deterministic Python. Builds the RAG support package and swaps staging "
     "into place."),
)


def _phase_table() -> str:
    rows = [
        "| Phase | Model calls | Prompt files in this pack |",
        "|---|---|---|",
    ]
    templates = dict(_PHASE_TEMPLATES)
    preambles = {
        "phase2_analysis": ("prompts/phase2.md",),
        "phase26_scorecard": ("prompts/scorecard.md",),
        "phase3_docs": ("prompts/phase3.md", "prompts/docs/<DOCUMENT>.md"),
    }
    for phase, kind, _what in _PHASE_WALKTHROUGH:
        files = [f"`prompts/templates/{n}`" for n in templates.get(phase, ())]
        files += [f"`{p}`" for p in preambles.get(phase, ())]
        if phase == "phase2_analysis":
            files.append("Nexus platform guide (immutable system context)")
        rows.append(f"| `{phase}` | {kind} | {'<br>'.join(files) or '--'} |")
    return "\n".join(rows)


def _phase_walkthrough() -> str:
    return "\n".join(
        f"### `{phase}` -- {kind}\n\n{what}\n" for phase, kind, what in _PHASE_WALKTHROUGH
    )


def _readme() -> str:
    catalog = load_doc_catalog()
    contract = default_contract(PLATFORM)
    return f"""# nexus-baseline

The standard Nexus documentation set, expressed as a pack.

This is the baseline: what a documentation run produces when you select no pack
at all. It is here so you can read it, and so you have something substantial to
fork instead of starting from a two-section skeleton.

**GENERATED. Do not hand-edit.** Regenerate with
`python scripts/generate_reference_pack.py` from the mcp-server directory. A test
fails if this drifts from the live document catalog and section contract.

## "Baseline", not "default"

Nothing loads this at runtime. When no pack is selected the *code* decides what
happens -- not this file. Registering this pack is never required, and doing so
does not change anything except making the same behaviour selectable by name.

The distinction matters because a copy that claimed to be the default would be a
second source of truth for what actually runs, and the two would drift. This
mirrors the default; it is not the default.

1. **To read.** The built-in behaviour is otherwise spread across
   `data/doc_catalog.yaml`, Python constants, `data/prompt_templates/`, and the
   per-platform section skeleton. Here it is in one repository, with immutable
   Nexus policy clearly separated from editable pack prompts.
2. **To fork.** If you want the standard documents with changes, start here
   rather than from the 2-section starter. Delete documents you do not need,
   trim `lens.sections.declare`, switch `mode` to `replace`.

## What it declares

- **{len(contract.analysis_section_names)} analysis sections** ({PLATFORM}; other platforms
  enforce the `GROUP_SUMMARY` floor alone)
- **{len(catalog.docs)} documents**, {sum(1 for d in catalog.docs if d.per_unit)} of them per-unit
- every phase running -- the most expensive configuration available

## The pipeline, phase by phase

Eight phases run in this order. Five issue model calls; three are deterministic
Python and have no prompt at all.

{_phase_table()}

{_phase_walkthrough()}
## The two prompt namespaces

`prompts/*.md` are **preambles** -- a block of text substituted into a
`${{PREAMBLE}}` slot inside a template. Editing one changes the framing.

`prompts/templates/*.md` are **prompt bodies** -- the entire prompt, matched to
the template filename the pipeline renders. Editing one changes the contract:
the unit-enumeration obligation, the output-size limit, the redaction and
fabrication rules all live here. A body override wins outright; the built-in
template is not consulted.

Three templates exist in the pipeline and are deliberately not shipped here:
`phase2_patch.md`, `phase3_patch.md` and `phase3_synthesis_patch.md` belong to
the living-docs patch lane, which has no pack context, so an override would
never be read.

## Immutable platform analysis

`reference/immutable-platform-analysis/*.md` contains byte-identical copies of
the Nexus-owned platform section guides. They are reference files, not prompt
slots: a plugin cannot replace how Nexus interprets Talend, Workato, Lobster,
or generic source. To add lower-authority guidance, create
`prompts/extensions/platforms/<platform>.md`; Nexus always applies its baseline
first and ignores any conflicting extension instruction.

## Narrowing it

The built-in set answers "explain this whole system to a new engineer." If your
question is narrower, most of that analysis is budget spent on questions you did
not ask. Switch to:

```yaml
lens:
  sections:
    mode: replace
    declare:
      - id: THE_ONE_THING_I_WANT
        question: >
          the specific question it must answer
```

and cut `docs` to the documents you actually read. A pack asking 4 sections
instead of {len(contract.analysis_section_names)} renders a Phase 2 prompt roughly
60% smaller, per group, per task.

## Where depth comes from

Not from `replace` -- that stops waste, which is a different thing. Depth comes
from `lens.sections[].question`, `lens.focus`, and above all
`prompts/docs/<DOCUMENT>.md`, which fully replaces the built-in instruction for
that document. If forked output is thin, look there before touching the phase
policy.
"""


def _files() -> dict[str, str]:
    return {"plugin.yaml": _manifest(), **_prompt_files(), "README.md": _readme()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="write nothing; exit non-zero if the committed pack is stale",
    )
    args = parser.parse_args()

    generated = _files()
    stale: list[str] = []

    for relative, content in generated.items():
        target = OUTPUT_DIR / relative
        current = target.read_text(encoding="utf-8") if target.is_file() else None
        if current == content:
            continue
        stale.append(relative)
        if args.check:
            print(f"STALE: {target}")
            print(
                "".join(
                    difflib.unified_diff(
                        (current or "").splitlines(keepends=True),
                        content.splitlines(keepends=True),
                        fromfile="committed",
                        tofile="generated",
                        n=1,
                    )
                )[:2000]
            )
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            print(f"wrote {target.relative_to(REPO_MCP_SERVER.parent.parent)}")

    if args.check and stale:
        print(
            f"\n{len(stale)} file(s) stale. Run:\n"
            "    python scripts/generate_reference_pack.py",
            file=sys.stderr,
        )
        return 1
    if args.check:
        print("reference pack is current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
