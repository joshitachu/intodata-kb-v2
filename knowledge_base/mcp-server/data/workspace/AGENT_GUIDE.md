# Agent guide

This is a map of your workspace: what each folder is FOR and how to move around it.
It is not a list of contents -- `indexes/` is that -- and it does not tell you what
the answers are. You work those out from the evidence.

## The one rule

`scan/` is the evidence of record, and it is addressable in full, at every phase,
by every skill. Everything under `facts/` and `indexes/` is a derived view that
names, in its own header, the artifact it came from. When a derived view and a scan
shard disagree, the shard wins and the disagreement is itself a finding worth
writing down.

Nothing in this workspace closes a route to the evidence. If an index does not
answer your question, open the shard. If the shard does not answer it, open
`scan/scan_result.json`. The only boundary is the workspace root: you may not read
or write outside it.

## Where things live

| Folder | Written by | What it is for |
|---|---|---|
| `scan/` | builder | The frozen scan. Sharded designs under `scan/jobs/<group>/<design>.json`, `scan/manifest.json` for the estate, `scan/scan_result.json` as the monolith of record. |
| `facts/` | builder | Deterministic tables derived from the scan: units, dependencies, interfaces, configuration, mappings, coverage, limitations. |
| `indexes/` | builder | Navigation aids: estate, systems, dependencies, unresolved. They help you find an artifact; they never decide which artifact you may open. |
| `evidence/` | builder | `code/`, `runtime/`, `external/`. Additional evidence lanes when a project has them. |
| `skills/` | method | How to work: investigate, analyse, score, produce, close out. Start at `skills/method.md`. |
| `rules/` | method | Grounding, evidence policy, source of truth, working memory, output conventions. These bind every skill. |
| `policies/` | method | Tool access, write permissions, publication. The limits you operate inside. |
| `templates/` | method | The shapes: group analysis contract, scorecard rubric, document set, working-memory entries. |
| `packs/` | method | Method variants for a specific platform or audience, when one is selected. |
| `knowledge/` | you | What stays true about this project across runs: terminology, decisions, lessons, known limitations, open questions. |
| `notes/` | you | Working memory for this run. Orientation, running observations, anything a later phase of yours will want back. |
| `findings/` | you | The durable result of your analysis this run: per-group analyses, summaries, the scorecard, recorded gaps. |
| `investigations/` | you | `active/` for open questions, `completed/` for settled ones. `completed/` survives the run. |
| `agent-state/` | you | Current goal, todo, checkpoint, recent actions. A resumed run reads this first. |
| `output/` | you | The published documents. |

A rebuild of the workspace replaces `scan/`, `facts/` and `indexes/` wholesale and
never touches anything you wrote.

## How to find something

1. Start from `indexes/estate.md` and `facts/units.json` to learn what exists and
   what it is called.
2. Narrow with `indexes/systems.md`, `indexes/dependencies.md`,
   `facts/dependencies.json` and `facts/interfaces.json`.
3. Open the thing itself: `scan/jobs/<group>/<design>.json` carries the components,
   the connections, the mapping data, the context groups and the sub-job calls for
   one design.
4. `scan/manifest.json` maps a design key to its shard path. Use that map rather
   than guessing a filename.
5. `facts/scan_coverage.json` and `facts/limitations.md` record what the scan did
   not capture. An absence there is a measured absence; an absence in a derived
   view is not.

## Your own memory is the handoff

There is no program between your phases. What you write into `notes/`, `findings/`
and `investigations/` is exactly what your later phases read back, unchanged.
`rules/working-memory.md` says which kind of thing belongs where. If a later phase
needs a fact, an earlier phase has to have written it down.

## Conventions

- Everything you publish is ASCII. See `rules/output-conventions.md`.
- Every claim you publish carries an anchor to the evidence it came from. See
  `rules/grounding.md`.
- A redaction marker such as `[PASSWORD_REDACTED]` means a secret was removed on
  purpose. It is not missing evidence, and it is never something to reconstruct.
