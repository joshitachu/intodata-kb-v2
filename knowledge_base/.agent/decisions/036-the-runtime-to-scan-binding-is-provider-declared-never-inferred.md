# 036 -- The runtime-to-scan binding is provider-declared, never inferred

**Status**: accepted, 2026-09-24

## Decision

Nothing above the provider boundary may know which runtime field names a scanned
design, or what one mirrored row means. A provider **declares** both:

```python
def static_ref_strategy(self) -> StaticRefStrategy | None      # source_field, match="exact"
def executable_semantics(self) -> Mapping[str, Literal["execution", "lifecycle"]]
```

Shared code reads the declaration and nothing else. `None` and `{}` are correct
answers, not failures. The resolution is **exact string equality** and there is no
normalising, casefolding, stripping, prefix or similarity tier anywhere downstream;
an ambiguous value is refused and counted, never resolved to the first, newest or
closest candidate. A human's mapping (`resolved_by="manual"`) is never overwritten.

## Why

**A fuzzy tier is worse than no binding, measured.** The frontend's
`joinHealthToJobs` had an exact -> normalised -> prefix ladder. On the PharmaDemo
estate the prefix tier produced **25 bindings, every one onto a job that genuinely
exists**: `DWH_Facts_001` -> `DWH_Facts_Master` (a different job, and one that was
failing at the time, so the wrong design would have been painted red);
`Daily_Queries_6_uur_old` -> the live `Daily_Queries_6_uur`;
`WMS_IE_stock_mirror_copy` -> `WMS_IE_stock_mirror`. This is the same hazard the
runtime lane already forbids for `InspectHub_GetLocation` / `InspectHub_GetLocations`.
A wrong binding attributes one design's failures to another and does so invisibly.

**The obvious shortcut is platform-specific.** `runtime_jobs.artifact_name` is the
right key for Talend and is populated only by `talend_tmc.py` and `mock.py`;
`elastic.py` never mentions an artifact at all (`grep -c artifact` returns 0), because
an Elasticsearch log document carries no reference to a published design. Reading that
column from shared code would have produced a feature that silently did nothing for
every other provider.

**"What does one row mean" is also per-platform, and getting it wrong inverts the
answer.** A Talend Route row is a *lifecycle*: `state="running"` means "deployed and
up", not "busy". 68 PharmaDemo route rows are `running`, so the execution rule reads a
perfectly healthy estate as failing. Meanwhile `_job_run_base` coerced
`executable_kind` to `JOB` for every provider, structurally excluding 49 of 158 rows
and leaving 14 of 28 business streams -- the entire ESB spine -- with nothing to show.

**The generic machinery already existed and was empty.**
`runtime_static_topology_snapshots` (platform-tagged; `dependency_type` is
`control_flow` or `data_lineage`), `runtime_job_scan_maps`, and a written, bounded,
cycle-safe BFS impact engine in `runtime/topology/correlation.py`. The binding table
had **zero rows**, so `correlate_execution` returned `unmatched` with no impacts for
every execution that has ever been mirrored. The only way to create a row was a
hand-written POST per task. Filling the reserved `static_ref_strategy()` slot is what
made the existing engine reachable; almost nothing new was needed.

## Consequences

- Binding coverage is honestly partial and must be shown as such. PharmaDemo binds 151
  of 177 runtime jobs, refuses 16 as ambiguous (designs that exist in two source roots,
  `JDE_DWH.../DATAWAREHOUSE` and `Talend8.../TALEND8`) and leaves 10 unmatched.
- A zero-binding estate is a finding, not a bug to be matched harder. `nexus_logs` has
  a ClientA scan against a PharmaDemo mirror (0 name overlap -- a project mis-binding);
  `workorders_demo` and `talend_demo` mirrors carry raw object ids in `artifact_name` because
  the TMC artifact lookup missed and `_to_job` fell back to the id.
- Unmeasured must render distinctly from healthy, with the reason stated.

## The key-space trap this exposed

`source_key` means two different things and they do not intersect. A **mirrored** row
(`runtime_jobs`, `runtime_job_runs`, `runtime_executions`) stamps the *provider* key --
measured: `tmc` on 1118 job rows and 88,965 execution rows, plus `mock` and
`demo-topology`. A **configured** row (`runtime_sources.source_key`) is
`{source_type}-{uuid}` -- `tmc-ddf1ed37af12`, `tmc-a335fb698715`. Resolving a mirrored
row's provider by joining `runtime_sources.source_key` therefore matches nothing, and
does so silently: every one of the 158 live PharmaDemo health rows reported
`liveness="unknown"`, restoring the exact defect the field exists to fix. Unit tests
passed throughout, because fixtures seeded a matching source row.

Resolve the provider key first and keep the `runtime_sources` join as a fallback.
`runtime/insights/repository.py::_batch_sources` still has the unfixed form of this
(it degrades to a missing optional enrichment rather than a wrong number).
