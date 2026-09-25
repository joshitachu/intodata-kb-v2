# 035 -- A zero must never be ambiguous

**Status**: accepted, 2026-09-23

## Decision

Any transform that can return "nothing" must distinguish *nothing was found* from
*nothing was looked at*, and must say which. A bare `0`, an empty dict or an empty
list is only a legitimate answer when the alternative reading has been positively
ruled out. Where it has not, the code returns an explicit state (`field_unmapped`,
`None`, a raised `SectionContractError`) instead.

## Why

This is one defect, found three times in unrelated protocols.

**Markdown parsing (run 258, project `ettm_TEST_Claude`).** The Phase 2 guide asked
for `### COMPONENTS`; the model wrote `## COMPONENTS`. Every parser keyed on the h3
regex matched zero sections and returned `{}`. Downstream could not tell that from a
genuinely empty analysis, so ~95% of the run's evidence was dropped without one
warning and Phase 3 fabricated code blocks to fill the gap. `section_parser.py` now
states the rule in its module docstring: *a transform that loses everything must
raise, never return empty.*

**Elasticsearch aggregation (2026-09, probe ladder against a production estate).** A
terms aggregation on an UNMAPPED field returns zero buckets rather than an error. So
`0` means both "this field does not exist" and "this field exists and is empty" --
and the second is a publishable finding about the estate while the first is a
question the investigator has not yet answered. `runtime/elastic_probe.py` resolves
field mappedness before returning any zero and yields `None` with a five-state
`ProbeResolution`, so a consumer that ignores the resolution cannot read a fabricated
zero. A genuine mapped-and-empty zero survives as `0` with `resolution=ok`; blurring
*that* would be the same defect pointing the other way.

**Missing evidence sections.** One analysis file per group made "nobody looked at
this group" and "somebody looked and found nothing" indistinguishable. Phase 2 was
split to one file per section so that a MISSING file meant nobody looked.

That split was REVERSED on 2026-09-24, and the reversal is worth recording because
it shows what the rule is really about. The per-section layout existed to serve two
Python consumers -- `bundle_analysis.py` and a `ls | grep -c` coverage gate -- and
both were deleted when the CLI lane moved to a method file. With nothing counting
files, 15 fragments per group bought nothing and cost coherence: a business rule was
written without the SQL that implements it in view. The layout is one
`_analysis/{group}.md` again, with the sections as H2 headings.

The INVARIANT survived the reversal intact; only its mechanism changed. A section
with no findings still gets its heading and a short paragraph stating what was
examined and what was not there. Measured on the run that prompted this
(`talend8_main`), both outcomes were present in the same estate: honoured --
"No database table/view or row schema is present. Data objects are files in remote
FTP directory `/out`... no file header, column type, nullable/key metadata appears";
degenerate -- a 5-byte file containing the bare word `None`, which records no search
and supports no grade. The Integration Suite confidence model names the distinction
exactly: absence is `Confirmed` about the search and `Inferred` about the thing.

## Consequences

- Every new reduction, parser, aggregation or filter is reviewed against this rule
  before merge, and the check belongs in its tests, not its docstring.
- Empty is a valid return only alongside evidence that the input was genuinely read.
  Prefer an explicit typed state over an in-band sentinel.
- The cost of the strict form is a louder failure on format drift. That is the point:
  the run-258 failure was silent, and silence is what made it expensive.

## See also

- `mcp-server/src/mcp_server/providers/custom/section_parser.py` (module docstring)
- `RAG_pipeline/backend/src/runtime/elastic_probe.py` (`ProbeResolution`)
- `mcp-server/data/investigation_methods/_shared/confidence-model.md` -- the same
  rule stated upstream and more precisely: "absence is not Confirmed".
- `mcp-server/data/generation_method/METHOD.md` -- where the missing-vs-empty rule
  now lives for the CLI lane, with both the honoured and degenerate examples.
  (`scripts/bundle_analysis.py` and its `BundleRetentionError` were the original
  third citation here; the file was deleted on 2026-09-24 with Phase 2.8.)
