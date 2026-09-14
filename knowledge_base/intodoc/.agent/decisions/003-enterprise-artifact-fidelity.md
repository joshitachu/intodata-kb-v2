# ADR-003: Enterprise Artifact Fidelity and the Sanitized Workspace

**Date:** 2026-09-09
**Status:** Accepted

## Context

A Talend export holds more than jobs, contexts, connections and routines.
Transformation logic also lives in TDM structures (`Structures/**/*.xml`),
XSLT stylesheets referenced from a route component parameter, and SOAP
service designs (`services/*.item` + `*.wsdl`). None of those were parsed,
so a route whose entire business rule sat in a stylesheet looked empty, and
the file itself was classified `UNKNOWN` and never even hashed.

Two further problems were structural rather than missing-feature:

1. **Coverage was unmeasurable.** A tMap offering 120 input columns and
   writing 8 read identically to one writing 118. Nothing stated how much
   of the offered evidence a mapper used, or which constructs the scanner
   could not model at all.
2. **Design references trusted a cache.** `tRunJob`'s `PROCESS` parameter
   and `cTalendJob`'s `SELECTED_JOB_NAME` are Studio label CACHES. Talend
   does not rewrite them when the child job is renamed, and the parser
   preferred them over `*:PROCESS_TYPE_PROCESS`, the child's stable
   repository GUID. A renamed child therefore resolved to nothing, or to a
   different design that now carried the stale name.

## Decision

1. **Parse the remaining artifact categories** into additive
   `ScanResult` collections (`structures`, `xslt_transforms`, `services`),
   each with an explicit `parse_outcome` (`parsed` / `partial` /
   `unsupported`) and, for structures, the list of element tags the parser
   does not model. An artifact is never dropped because it was only
   partly understood.
2. **Bind components to those artifacts by exact token only**
   (`platforms/talend/resource_refs.py`). A reference is matched against a
   resource's repository path or file name; a reference naming a file the
   scan does not contain is recorded `unresolved` with the reason, because
   "the transform is missing" is a finding.
3. **Publish two deterministic ledgers.**
   `mapping_accounting.py` answers "offered / written / mapped / constant /
   lookup / dynamic" per mapper component, for all four Talend mapping
   formats. `capability_ledger.py` tallies every construct the scan saw
   with a capability status, and accounts for every walked file
   (`file_outcomes` sums to the inventory row count). Both are pure Python
   and are built BEFORE the redaction verification gate, so a derivative of
   verified data is never published unverified.
4. **Resolve design references identity-first.** The GUID wins; the label
   is a documented fallback (`reference_source: identity | label | none`),
   and a disagreement between the two is a `stale_label` warning rather
   than a silent normalization. `USE_DYNAMIC_JOB` yields
   `resolution="dynamic"` with no target, because a runtime-selected child
   has no static answer.
5. **Ship a sanitized workspace** (`scan/sanitized/` + its manifest): a
   redacted mirror of every textual source the walker inventoried. The
   parsed models are a projection of what the scanner models; a consumer
   needing the exact serialized form of a construct had nowhere else to go.

## Rationale

- **A gap must be a row, not a silence.** Every construct the scanner
  cannot model now has an explicit status. `offered_enumerable=false` on an
  `id_Dynamic` schema says the field count is a lower bound instead of
  publishing a confident wrong number.
- **Absence of a parameter is not evidence of its value.** `ACTIVATE` is
  omitted on an active component, so `active` derives from absence;
  `TRANSMIT_WHOLE_CONTEXT` and friends are `bool | None` so "the export did
  not say" stays distinguishable from "the export said false".
- **A dynamic value is not an identity.** A `context.*` JMS destination
  collapses to the existing non-identity placeholder, so neither the
  manifest's connection nodes nor the landscape's channel identities can
  fabricate a shared dependency between routes that resolve to different
  queues per environment. Same rule the connection deduplicator already
  applied to redacted hosts.

## Consequences

- **Publishing raw text needs the structural redaction rule at text
  level.** Layer 1's key/value pattern is XML-attribute shaped with a fixed
  key list; the structural layer redacts by token-aware variable NAME. A
  Talend context item's `name="DbPassword" value="<plaintext>"` is caught
  only by the latter, which never touched the source file. The mirror
  therefore applies `CredentialFilter.redact_named_value_pairs` and then
  refuses to write any file whose sanitized text still trips
  `detect_residual`. Exclusions record the finding CATEGORIES, never values
  and never hashes of values.
- **Plain configuration text is outside Layer 1's design.** A
  `datasource.password=...` line in a `.txt`/`.json`/`.properties` file
  passes both `filter_text` and `detect` untouched (verified).
  `detect_key_value_secrets` closes that gap as a GATE for the mirror; it
  is deliberately not applied to XML/Java content, where it would flag
  ordinary code such as `String password = decoded.substring(0, i)`.
- Binary files are never mirrored: redaction cannot be verified on bytes
  the filter cannot parse, so a "sanitized" binary would be an unchecked
  claim.
- The two ledgers are additive artifacts; `scan_result.json` stays readable
  by existing consumers because every new field defaults.

## Follow-ups (not done here)

- Layer 1 and the structural layer still use different sensitive-name
  vocabularies. `redact_named_value_pairs` is applied only by the mirror;
  folding it into `filter_text` would redact more on every scan and needs
  its own verification pass.
- `ContextGroup` and `MetadataConnection` carry no source path, so those
  files stay `indexed` rather than `parsed` in the inventory accounting.
- TDM maps under `Maps/` have no export-version selection (jobs, contexts,
  routines and structures all do).
