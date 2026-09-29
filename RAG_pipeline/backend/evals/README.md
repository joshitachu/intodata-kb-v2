# Answer-quality eval harness

Scores what the RAG / agent path *answers*, not what it retrieves. It exists
because the system's real failure mode is not "returns nothing" — it is
"returns something confident and incomplete", and nothing in the codebase
measured that.

Runs offline against recorded answers (CI) or live against a deployed backend.
No imports from `src/`: the suite must survive a retrieval or harness rewrite.

---

## What this is not

`backend/benchmark/` already exists. It is a different thing and stays: a
live-only corpus of Q&A pairs scored with `precision@k` plus one 1–5 LLM
relevance score. It has no notion of an unanswerable question, no abstention
metric, no groundedness check, and it cannot run without Qdrant, Postgres and
Ollama. This harness covers the gap; it does not replace it.

---

## Running it

```bash
cd RAG_pipeline/backend

# offline, the default -- scores the recorded baseline fixture
python -m evals run

# any other fixture file
python -m evals run --fixtures evals/fixtures/reference_ideal.json
python -m evals run --fixtures evals/fixtures/always_declines.json

# machine-readable, for CI
python -m evals run --format json > eval-result.json

# one case, with per-case detail
python -m evals run --case pharma-svd-customer-selection-rule --verbose

# check the suite is well-formed and every evidence file still exists
python -m evals validate
python -m evals cases

# add the semantic judge (needs Anthropic credentials; falls back silently)
python -m evals run --judge auto

# against a running backend, recording the answers as a new fixture
python -m evals run --live \
  --base-url http://localhost:8000 \
  --access-token "$ACCESS_TOKEN" \
  --record evals/fixtures/live_2026-09-20.json
```

Exit codes: `0` all cases passed, `1` at least one deterministic failure,
`2` the run could not be performed.

As pytest:

```bash
pytest evals/                 # or just `pytest` -- evals is in testpaths
```

### Live mode prerequisites

`POST /api/v1/query` requires a JWT `access_token` cookie and is rate limited to
30 requests/minute. Log in first and pass the cookie value as `--access-token`;
the runner throttles to ~2.1 s between calls on its own.

---

## The metrics

Reported separately, each with its own denominator. A metric that is undefined
for a case is excluded from that metric's average — never folded in as a 0 or a
1.

### Deterministic — no model, no network, reproducible. **These are the gate.**

| Metric | Measures | Undefined when |
|---|---|---|
| `retrieval_hit_rate` | Answerable cases where the declared evidence file appears among the retrieved sources. Was the answer even retrievable? | case declares no `expected_sources`, or the answer never recorded sources |
| `groundedness_numeric` | Fraction of integers in the answer that also occur in the retrieved text. Catches invented counts. | no retrieved context, or the answer states no gradeable numbers |
| `citation_validity` | `[N]` markers that resolve to a retrieved source | the answer cites nothing |
| `abstention_recall` | Unanswerable cases where the system declined **and** did not report absence-from-corpus as absence-in-reality | no unanswerable cases in the selection |
| `over_abstention_rate` | Answerable cases the system declined instead of answering | no answerable cases |
| `coverage` | Mean K-of-N expected entities named | case declares no `expected_entities` |
| `undisclosed_partial_rate` | Enumerations answered incompletely with no signal that the list was incomplete | as above |
| `count_accuracy` | Cases stating every expected count, and not stating a declared near-miss in place of it | case declares no `expected_counts` |
| `contradiction_disclosure` | Cases where the corpus disagrees with itself and the answer surfaced every side | case declares no `contradiction` |
| `must_not_claim_violation_rate` | Cases matching a forbidden assertion | never |
| `pass_rate` | Cases with zero deterministic failures | never |

### Judge-dependent — informational, never a gate

| Metric | Measures |
|---|---|
| `groundedness_judged` | Claim-level support of the answer by the retrieved context (semantic, not numeric) |
| `correctness_judged` | Agreement with the case's prose ground truth |

Two reasons the judge cannot gate:

1. **It needs credentials.** CI does not have them, and an eval that cannot run
   in CI rots.
2. **It is not reproducible.** Claude Opus 5 runs adaptive thinking and no longer
   accepts `temperature`, so repeated runs can differ. A regression gate on a
   non-reproducible number produces flaky builds and gets switched off.

`--judge auto` uses Claude when credentials resolve and otherwise falls back to
`HeuristicJudge`, a lexical content-word overlap proxy that is always available.
The fallback is labelled `confidence="deterministic-proxy"` and the report prints
which judge ran. **Do not read a heuristic 0.7 as "70% grounded"** — it measures
vocabulary overlap, not entailment.

### Why abstention is two numbers

A single "abstention correctness" score is gameable: answer "I don't know" to
everything and it goes to 100%. `evals/fixtures/always_declines.json` is that
adversarial system, and running it is the proof:

```
abstention_recall      100.0%   <- perfect
over_abstention_rate   100.0%   <- and useless
pass_rate                0.0%
```

### The distinction the suite turns on

Two things that look identical to a keyword matcher:

- *"There is no customer filter."* — a claim about the client's reality.
  Fabricated, because the system only searched documentation.
- *"The indexed documentation does not describe a customer filter; the rule, if
  there is one, would be in the Talend scan."* — a claim about the search scope.
  Correct.

`detect_unqualified_absence` grades this per sentence: an absence assertion is a
violation unless the *same sentence* scopes it to the document set, or negates
it (*"that does not mean there is no selection"*). A qualifier three paragraphs
later does not launder a bare claim, because the bare sentence is the one a
reader quotes back to the business.

---

## Fixtures and honesty about numbers

Every recorded answer carries a `provenance`:

| Provenance | Meaning |
|---|---|
| `live_capture` | captured from a real run. **The only provenance that is a measurement.** |
| `reconstructed_from_incident` | the decisive sentence is observed wording; the surrounding prose is not |
| `handwritten_reference` | authored by hand as a positive control |
| `handwritten_negative` | authored by hand to exercise one failure mode |

If a run is not 100% `live_capture`, the text report opens with
`!! NOT A MEASUREMENT OF THE LIVE SYSTEM !!` and the JSON report carries
`"is_measurement": false`.

**The three fixtures shipped here contain no measured numbers.** They prove the
scorer detects the four failure modes; they say nothing about how the deployed
system performs. To get a real baseline, run live with `--record`; the resulting
file will be all `live_capture` and the banner will flip.

| Fixture | Expected result | Purpose |
|---|---|---|
| `reference_ideal.json` | 100% pass | positive control — if a case here fails, the scorer got stricter than correct |
| `observed_baseline.json` | ~36% pass | one case per failure mode, plus passing controls |
| `always_declines.json` | 0% pass | adversarial control for the abstention split |

---

## Adding a case

1. **Find the ground truth in a file and transcribe it.** Do not paraphrase
   counts. `evidence[].path` is relative to the repository root and
   `python -m evals validate` fails if it moves.
2. **Decide `answerable` honestly.** It means *"a correct system can answer this
   from the retrievable document corpus"* — not *"the fact is true"*. The SVD
   case is `answerable: false` even though the rule definitely exists, because it
   lives in `knowledge_base/intodoc-output/.../scan/jobs/*.json` and not in
   `knowledge_base/output/PHARMA_DEMO/docs/`. Mark the out-of-corpus evidence
   `in_corpus: false`; the validator enforces that every unanswerable case has at
   least one.
3. **Pick the assertions that match the failure you are guarding against:**

```yaml
- id: my-case
  project: CLIENTA_FULL_LANDSCAPE
  answer_type: enumeration      # enumeration | count | lookup | explanation | contradiction
  answerable: true
  question: >-
    ...
  ground_truth: >-
    ...
  evidence:
    - path: knowledge_base/output/.../doc.md
      locator: "section 1.2"
      in_corpus: true
  expected_sources: ["doc"]     # filename substrings, all must be retrieved
  expected_entities:            # -> coverage
    - {name: "4FS", aliases: ["Dynamics 365"]}
  min_coverage: 1.0
  expected_counts: {things: 9}  # -> count_accuracy
  forbidden_counts: {things: [5]}
  must_not_claim: ["\\bno filter\\b"]
  required_disclosures:         # OR within a group, AND across groups
    - ["documentatie", "documentation"]
  contradiction:
    sides:
      - ["\\b31\\b", "eenendertig"]
      - ["\\b30\\b", "dertig"]
```

Notes that will save you an hour:

- Patterns are matched against **normalised** text: lowercased, accents stripped,
  and markdown punctuation (`* _ ` # > |`) removed. A pattern containing a table
  pipe can never match.
- `aliases` are not optional polish. Without `"Dynamics 365"` on the `4FS`
  entity, a correct answer scores as a coverage miss and the metric becomes
  noise.
- `forbidden_counts` only fires when the *correct* value is absent. That is
  deliberate: the right answer to the FO 4.1 field count is "31, and note the
  register says 30", which states a forbidden value on purpose.
- Keep the answerable:unanswerable ratio honest. A suite of in-corpus questions
  measures only how well the system answers questions it can answer.

4. `python -m evals validate` then add an entry to `reference_ideal.json`
   (what a correct answer looks like) and, if you are pinning a real defect,
   to `observed_baseline.json`.

---

## Files

| Path | Contents |
|---|---|
| `cases.py` | eval-set schema and YAML/JSON loader |
| `answers.py` | `RecordedAnswer`, `FixtureProvider` (offline), `HttpProvider` (live) |
| `scoring.py` | all deterministic metrics; pure functions, no I/O |
| `judge.py` | `Judge` protocol, `HeuristicJudge` fallback, `ClaudeJudge` |
| `runner.py` | orchestration and metric aggregation |
| `report.py` | text and JSON rendering |
| `__main__.py` | CLI |
| `suite/core.yaml` | the eval set |
| `fixtures/*.json` | recorded / authored answers |
| `test_eval_suite.py` | tests of the harness itself |

## Known limitations

- `groundedness_numeric` scores *derived* numbers (percentages, sums the model
  computed) as ungrounded, because they legitimately do not appear in the
  retrieved text. That is why it is reported and not gated.
- Short entity aliases match on word boundaries, so a two-letter worktype code
  like `SO` can in principle match an English "so". The corpus and the questions
  are Dutch, which makes this rare; if it bites, lengthen the alias.
- Abstention and absence detection are lexical. They are strong on the Dutch and
  English phrasings this corpus produces and will need extending for a third
  language.
- The live provider covers `POST /api/v1/query` only. The agent chat path
  (`/api/v1/conversations/{id}/messages`) is a different, tool-using surface and
  needs its own provider before it can be scored here.
