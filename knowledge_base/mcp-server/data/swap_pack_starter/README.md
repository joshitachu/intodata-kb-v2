# Nexus Pack Starter

A pack tells Nexus what to look for and what to write. This repository is the
template every pack starts from.

## Two ways in

**Author it in Nexus.** Create a pack in the Packs workbench, by hand or with
the AI author. It is stored against your account and is yours alone -- nobody
else can see or run it. Edit and run it as much as you like.

**Share it.** When a pack is good enough that everyone should have it, publish
it. Nexus pushes it to this repository as a branch and opens a pull request.
Someone reviews it. If they merge it, the pack becomes available to every user.

Publishing asks; merging grants. That split is deliberate: authoring stays open
to everybody, and what everybody *receives* stays reviewed.

**Or work in git directly.** Clone this repository, branch from `main`, edit,
push, and register the branch from the Packs workbench. Same format, same
result -- useful when you would rather use your own editor.

Each branch is its own pack. Keep `main` as the clean base.

## What a pack can change

Extraction is platform-owned: parsing Talend, Workato or generic source into a
scan cache is identical for every pack, and no pack can weaken it. What a pack
changes is what happens to that evidence.

Platform analysis is also Nexus-owned. The Talend, Workato, Lobster, and
generic analysis guides are always supplied as immutable system policy. A pack
cannot replace or suppress them. Optional additions go in
`prompts/extensions/platforms/<platform>.md`; Nexus applies them after its
baseline and ignores conflicts.

| Block | Controls |
|---|---|
| `phases` | whether each analysis phase runs, and with which model |
| `lens.focus` | which evidence categories get screened in |
| `lens.sections` | which analysis sections exist, and the question each asks |
| `lens.requirements` | standing rules injected into the analysis prompt |
| `docs` | which documents come out, and what feeds each |
| `audience` | how findings are presented (never what is found) |
| `quality_gates` | advisory size and content checks |

`lens.sections.mode` is the important one:

- `extend` adds your sections to the built-in analysis contract.
- `replace` makes your sections *the* contract. The analysis prompt asks for
  exactly these and nothing else, so a focused pack stops spending its budget
  on built-in questions it never wanted.

`GROUP_SUMMARY` is a protected floor and survives either mode.

## Where depth comes from

Not from `replace` -- that stops waste, which is not the same thing. Depth comes
from:

- **`lens.focus`** — a narrow screen means more attention per retained item.
- **`lens.sections[].question`** — a section with a vague question produces a
  vague section. This is the highest-leverage field in the file.
- **`prompts/docs/<NAME>.md`** — fully replaces the built-in instruction for
  that document. The single best place to raise output quality.
- **`examples/*.md`** — a finished document to imitate. Style only; never
  treated as evidence.

If a pack's output is thin, the cause is almost always here rather than in the
phase policy.

## Files

```
plugin.yaml              the manifest -- fully commented, start here
prompts/phase2.md        replaces the analysis preamble
prompts/phase3.md        replaces the document-writing preamble
prompts/docs/<NAME>.md   per-document instruction (replaces the built-in)
prompts/extensions/platforms/<platform>.md  additive platform guidance
examples/*.md            style exemplars
```

A missing block or file means "use the built-in behaviour" -- never "produce
nothing".

## Overriding a whole prompt

The files above are *fragments*: a preamble is substituted into a slot inside a
larger prompt. You can also replace an entire prompt, for any phase, by putting
a file in `prompts/templates/` named after the template it replaces:

```
prompts/templates/phase1_extraction.md            Phase 1 evidence extraction
prompts/templates/phase1_chunk_selection.md       Phase 1 chunk selection
prompts/templates/phase1_manifest_extraction.md   Phase 1 extraction (manifest mode)
prompts/templates/phase2_analysis.md              Phase 2 analysis contract
prompts/templates/phase2_claims.md                Phase 2 claim extraction
prompts/templates/scorecard.md                    Phase 2.6 scorecard
prompts/templates/phase3_doc.md                   Phase 3 document writing
prompts/templates/phase3_unit_doc.md              Phase 3 per-unit dossier
prompts/templates/phase37_business_topology.md    Phase 3.7 topology synthesis
```

Note `prompts/scorecard.md` and `prompts/templates/scorecard.md` are different
files: the first is a paragraph inserted into the prompt, the second *is* the
prompt. Putting a whole prompt in a preamble slot produces a pack that looks
complete and renders a duplicated, unframed prompt.

**A body override replaces the built-in outright** -- anything you leave out is
gone, including the unit-enumeration obligation and the redaction and
fabrication rules. So do not write one from scratch: in the workbench, use
*Add prompt override*, which starts the file from the current built-in text.
Working in git instead, copy it from the `nexus-baseline` branch, which carries
every one of these files verbatim.

Three prompts exist that a pack cannot override: the patch prompts used when
documents are amended in place rather than regenerated.

## Failures

A declared section the evidence cannot answer is not an error. A security pack
pointed at code with no dynamic SQL *should* come back empty for that section --
that is a correct result. Those gaps are recorded in
`_assessment/pack_section_coverage.json` for you to inspect, and the documents
publish clean. Nothing is stubbed and no run is failed over an empty section.

## Do not commit

Credentials, customer secrets, or private source code. A pack is instructions,
not data.
