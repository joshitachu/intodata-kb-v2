# Memory Index

- [Topology UNAVAILABLE semantics](project_topology_unavailable_semantics.md) — why the harness scope gate is allowed to deny out-of-scope topology reads instead of collapsing them into UNAVAILABLE
- [Never stream unvalidated answer prose](feedback_no_unvalidated_answer_streaming.md) — answer text waits for the grounding validator; only server-authored progress may stream
- [Chat latency is decode-bound](project_chat_latency_is_decode_bound.md) — measured baseline: ~99.9% of a 24s turn is Kimi decode; retrieval is noise
- [v2 cohorts bypass the provider layer](project_v2_cohorts_bypass_provider_layer.md) — live chat builds its own ChatOpenAI, so cost/token/latency accounting must live in src/harness/**; open Kimi pricing question
- [Landscape rollup has no jobs](project_landscape_rollup_has_no_jobs.md) — job/component questions need the graph-manifest endpoints; the rollup's silence caused a silent document-substitution defect
- [Envelope depth blocks relationship targets](project_envelope_depth_blocks_relationship_targets.md) — two harness sanitizer depth constants need +1 before pair-view navigation chips work
- [Kimi k2.7-highspeed evaluation](project_kimi_k27_highspeed_evaluation.md) — measured: quality is BETTER than k2.5, but 10-15x dearer, TTFT 3x worse, 21% truncation, and it 400s on the current provider wiring
