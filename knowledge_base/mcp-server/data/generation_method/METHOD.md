# Nexus documentation method

You are the orchestrator of one documentation run. This file is the method. The
message that started the run carries only the facts that method cannot know --
where the workspace is, how big this estate is, what a previous attempt already
finished. Everything about HOW the work is done is here.

Read this file end to end before you spawn anything.

ASCII only, everywhere. Generated text reaches Windows cp1252 consumers.

## 1. The shape of the run

Four phases, and they are not interchangeable:

| Phase | Purpose | Audience |
|-------|---------|----------|
| 1 SETUP | Take stock of the staged workspace. Read the guides. | you |
| 2 GROUP ANALYSIS | Extract everything the evidence contains, per group. | a retriever |
| 3 WRITE DOCS | Select, order and explain, per document. | a person |
| 4 PUBLISH | Index the run, record what remains open. | the server |

The distinction between Phase 2 and Phase 3 is the single most important thing
in this file, and it is stated once in each place it governs. Get it wrong and
the run either publishes padding or loses evidence.

## 2. The workspace

Everything is pre-staged. The scan has already been parsed, credential-filtered
and reduced to markdown; you are reading the export, not the raw repository.

```
<staging>/
  _preprocess/            evidence and guides (read-only to you)
    {group}.md            ALL scan data for one group; {group}_part1.md / _part2.md if large
    _analysis_guide.md    the section contract for Phase 2 -- the list of sections lives HERE
    _phase3_guide.md      cross-group synthesis guidance for Phase 3
    _doc_templates.md     required section structure per document -- read BEFORE Phase 3
    _ref_core*.md         reference document templates, per Phase 3 agent
    _ref_business_ops*.md
    _ref_user*.md
    _scoring_rubric.md    scoring reference (8 dimensions x 5 levels)
    _anti_patterns.md     AP-xxx-nnn catalogue
    _red_flags.md         RF-xxx-nnn catalogue
    _security_checklist.md
    _pattern_library.md
      These five are staged from the pinned method library
      (data/investigation_methods/_shared/). Their digests are verified at load
      and a mismatch fails the run. Five of the seven files there match no
      upstream revision of the project they descend from -- they were adapted
      for Talend and carry the AP-xxx-nnn / RF-xxx-nnn / P-ERR-nnn identifier
      schemes this method cites, which upstream does not use. Cite an ID from
      the catalogue in front of you, never one you remember from elsewhere.
  _method/
    METHOD.md             this file
    _shared/              the confidence model and the validation checklist
    <platform>/           platform parsing guides, when this platform has them
  _assessment/
    quick_scan/{group}.md pre-computed risk flags and priority rank for ONE group
    quick_scan.md         the cross-group roll-up
    phase2_work_plan.md   every group with evidence size, design count, flag counts,
                          the suggested batching onto workers, and the per-kind
                          breakdown of the design total
    scorecard.md          written by Phase 2.6, or by the server when it is disabled
  _analysis/              YOU write here in Phase 2
    {group}.md            the group analysis -- ONE per group, and the only
                          kind of file here that counts as a group analysis
    {group}_summary.md    that group's navigation aid, not an analysis
    _shared_library.md    the shared-library digest; underscore = not a group
  _provenance/            YOU create this; one JSON sidecar per document
  _knowledge/             YOU create this; the run's open-questions ledger
  _tools/runtime_tools.py the live runtime query surface (see section 4)
  *.md                    YOU write the published documents here in Phase 3
  logs/
```

Create the two run-record directories once, at the start, before spawning
anything: `mkdir -p <staging>/_provenance <staging>/_knowledge`. Every other
directory already exists. The leading underscore is what keeps both of them out
of the client corpus -- an unprefixed `knowledge/` is published as a delivered
document.

### The boundary

PROHIBITED: do not read any files outside the staging directory. This is a
security boundary and it is absolute. It says nothing about which files INSIDE
the staging directory you may read: inside it, read whatever your claims need.

Do not call MCP tools -- `get_scan_status`, `get_chunks`, `fetch_json`,
`fetch_chunk`, `trigger_scan`, `get_analysis_prompts`, `get_reference_docs`,
`insert_to_rag`. Everything is pre-bundled. The runtime script in section 4 is
a local script run with Bash, not an MCP tool, and it is not an exception to
this rule.

Do not use TodoWrite. Do not run ASCII/Unicode validation scripts. Do not read
`.agent/` docs, `CLAUDE.md` or `mcp-workflow.md`. Do not create `_scratch/` or
other intermediate directories.

### Reading files without losing evidence

For a file over 500 lines, do not Read it in one call. Walk it in successive
windows with `Bash sed -n 'M,Np' file` (or `tail -n +M | head -N`) until you
reach the end of the file. A single `head -N` reads the first window and
silently leaves the rest of your evidence unread; that is how a document ends
up describing an estate it never saw.

Read in parallel as widely as the work needs. If a batch of reads fails, retry
the failed reads individually -- a failed call is a call to repeat, never a
reason to proceed without the evidence.

## 3. Evidence discipline

Two files under `_method/_shared/` govern how findings are graded and checked.
They are the vocabulary of this run. Read them; do not paraphrase them, and do
not restate their rules anywhere in your output.

- `_method/_shared/confidence-model.md` -- Basis (`Confirmed` / `Inferred` /
  `Unknown`, per finding, mandatory), the document Ceiling (derived from the
  input class, never hand-set) and Aggregation (the minimum, never the
  product). Two of its rules decide most real arguments in this work: a
  finding graded `Confirmed` must carry a locus, and absence is not
  `Confirmed` -- a search returning nothing is `Confirmed` about the search
  and `Inferred` about the thing's actual absence.
- `_method/_shared/validation-checklist.md` -- the pre-delivery self-check.
  Section A is what you run before you publish. Use its severity
  classification when you grade a finding.

A code scan is a `STATIC ANALYSIS` input class. Where this run also reaches the
runtime registers (section 4) for a given unit, that unit's findings may reach
`RUNTIME-VALIDATED`. Take the ceiling from what you actually had, per document.

If `_method/<platform>/` exists, it holds the versioned parsing method for this
platform -- what its artifact formats mean, which facts an export can carry and
which are runtime-only. Read `profile.md` first; open the rest when a question
about the platform itself comes up. A platform with no directory there has no
platform-specific method, and the two shared files above are the whole method
for this run. Never apply one platform's parsing guidance to another: confident
wrong instruction produces absences that are artefacts of the wrong method.

### The form of a locus

A locus is a FILE AND A LINE RANGE, written so that a reader can open it
without guessing. Two forms, both fine, and nothing else:

```
`_preprocess/Collectors.md:7524-8164`
`_preprocess/Collectors.md`, lines 7524-8164
```

- ALWAYS write the path from the staging root. `_preprocess/Staging.md:1-27`
  resolves; `Staging.md:1-27` does not -- three different directories hold a
  `Staging.md`, and the line numbers are only right for one of them.
- Several spans separated by commas is the normal way to cite scattered
  evidence: `_preprocess/Global.md:1613-1650,1767-1795,1937-1959`.
- A single line is `:42`. The last line of a file is `-EOF`.
- Citing a file with no line range at all is a weaker locus. It is allowed
  where the whole file is the point, and it is the wrong answer for a quoted
  expression: give the range you copied it from.

This IS checked, deterministically, after the run: every cited path is opened
and every cited range is compared against the file's length. What the check
CANNOT do is read your claim -- it establishes that the locus resolves, never
that the cited lines say what you said they say. That part stays on you.

### Copy, never reconstruct

Every quoted code block, expression, SQL statement, schema column, context
variable, endpoint and configuration value must be COPIED character for
character out of the evidence in front of you. If you did not copy it, do not
quote it.

In a published run only 16% of the quoted code lines existed verbatim anywhere
in the scan. The other 84% were reconstructed from memory: invented namespaces,
invented context variables, invented component types -- indistinguishable from
facts to the reader, and wrong. If you cannot find the literal text, write what
IS established and record the gap. A plausible-looking reconstruction is the
worst outcome available to you; an honest gap is a correct one.

### Unresolved evidence -- doubt has somewhere to go

Across 302 KB of published output, a previous run said "unavailable"
essentially zero times. That was not confidence. There was no channel for
doubt, so doubt was published as confident prose.

In a document, "not determinable from the scanned sources" is a CORRECT and
wanted answer. Say which design and which component the gap sits in, and what
IS established. Runtime behaviour, throughput, live schedules and operator
intent are routinely not determinable from a code scan. Say so.

In the run ledger, APPEND one entry per open question to
`<staging>/_knowledge/open-questions.md`:

```
## <the question, one line>
- Document: <document name>
- Scope: <group / design / component>
- Established: <what the evidence does show>
- Would settle it: <the exact evidence needed>
```

Append with a heredoc, never the Write tool -- several agents write that file
in parallel and a read-modify-write destroys the others' entries. Put the
closing `EOF` at the start of its own line; an indented terminator does not end
a heredoc.

```
Bash cat >> <staging>/_knowledge/open-questions.md <<'EOF'
... entry ...
EOF
```

The same append rule covers `<staging>/_knowledge/decisions.md`, where you
record the choices you made about how to run the work.

## 4. Runtime evidence -- the live estate

The scan says what the code does. It cannot say what ran, how often, or what it
did when it failed. That evidence lives in the runtime registers -- the
execution history and the log estate -- and this run can query them through a
script staged inside the staging directory:

```
Bash python3 <staging>/_tools/runtime_tools.py list
Bash python3 <staging>/_tools/runtime_tools.py call <tool> '<json>'
Bash python3 <staging>/_tools/runtime_tools.py elastic <probe> '<json>'
```

START WITH `list`. It returns every available tool and probe with its JSON
input schema. Read that output and pick; do not guess a tool name, and do not
guess an argument. Every call is recorded in this run's probe ledger, and a
figure in a published document that no probe returned is reported against the
run afterwards.

If the script is not there, no runtime source is connected for this project.
Say so where a runtime fact would go, and carry on. That is not an error.

1. EVERY PUBLISHED RUNTIME NUMBER CARRIES ITS MEASUREMENT REFERENCE.
   Say which register or index it came from, which table or aggregate, the time
   window it covers, and the denominator it was divided by. The SAME estate
   reads as 0.087% failing or 30.8% failing depending on which register the
   number covers and what it is divided by -- a factor of 350. "Over the last
   30 days the PROD execution register recorded 17 failed runs out of 2,122" is
   a fact. "Fails 23% of the time" is not.
2. NEVER MIX ENVIRONMENTS IN ONE RATIO.
   A failure rate computed over a register that blends PROD and ACC describes
   no environment that exists. Measured on one estate: 23.1% unscoped, while
   PROD alone is 0.8% and ACC is 60.7%. Establish which environments a register
   covers BEFORE you quote any rate over it, and name the environment the
   number describes.
3. ZERO IS AMBIGUOUS -- TREAT IT AS A QUESTION, NOT AN ANSWER.
   A count of zero means either "this field does not exist" or "the field
   exists and is empty". Those are different facts and must never be
   conflated. The tools report `field_unmapped` explicitly; if you got a bare
   zero you have not yet established which of the two it is.
4. ESTABLISH PRESENCE WITHOUT READING CONTENT.
   To show that message payloads carry real business content, use the SIZE
   DISTRIBUTION rather than retrieving a message: an envelope is a few hundred
   bytes, a real business message is tens of kilobytes. The tools deliberately
   cannot return document content. That is a confidentiality boundary, not an
   inconvenience, and there is no route around it to look for.
5. ABSENCE IS A FINDING, AND IT IS STATED PLAINLY.
   If the runtime estate holds no data for a unit, say so and say what you
   checked. Low coverage is EXPECTED and correct: the runtime source lists
   SCHEDULED TASKS, not the artifact inventory. A design invoked by a parent or
   embedded as a reusable component runs constantly and never appears as a task
   of its own. Absence from the runtime source is NOT evidence that a design
   does not run, and writing it as if it were is a false statement about the
   client's estate.

The documents state the findings; they never state the mechanics. The CLIENT
PROSE rule in section 6 covers this script exactly as it covers every other
internal name: no script or tool names, no file paths, no phases, no sub-agents
in a published document. "The interface ran 2,122 times in the last 30 days, 17
of them failing" is right; naming the command that established it is not.

## 5. Phase 2 -- group analysis

### PHASE 2 IS FOR THE RETRIEVAL CORPUS. PHASE 3 IS FOR THE READER.

Nobody reads these analyses end to end. A retriever does, one passage at a
time, so there is no length to aim at and no byte budget to stay under. Be
EXHAUSTIVE: name every design, record every business rule, copy every SQL
statement and every custom-code body verbatim, and list every context variable
and every schema column. A fact left out here is a fact no document downstream
can cite, because this is the only place it was ever written down.

Depth scales with the evidence, and you are the one holding it. A group
carrying two megabytes of evidence warrants far more analysis than one carrying
six kilobytes; writing them to the same length is the failure. Deciding what to
leave out is editorial judgement, it belongs in Phase 3 where a person reads
the result, and it does not belong here.

If you still cannot cover a group fully, EVERY job in the group must still be
named, with its components and its role -- shorten the prose, never drop a
unit. A unit missing here is a unit missing from every document downstream.
Record the depth you had to sacrifice in the open-questions ledger.

### How to run it: batch, do not fan out

Sub-agents do NOT run as wide as you spawn them. Measured: 20 groups requested
in parallel, 3-4 actually running at a time, analyses landing one or two a
minute for 16 minutes, 5 groups never analysed and Phase 3 never reached. One
sub-agent per group therefore SERIALISES this phase. Batch instead: fewer
workers, each covering several groups.

1. Read `<staging>/_assessment/phase2_work_plan.md`. It lists every group with
   its evidence size, design count and pre-computed risk flags, a suggested
   split of the groups across workers, and -- where this estate has reusable
   designs -- a shared-library pre-pass to run first.
2. If the plan lists a shared-library pre-pass, spawn that ONE worker first and
   let it finish. It reads only the reusable designs named there and writes the
   interface digest to `<staging>/_analysis/_shared_library.md`. It writes NO
   group analysis: a reusable design is still documented in full by the group
   that owns it. The leading underscore is what says so -- see the `_analysis/`
   naming rule in the coverage section below. Every group worker reads that
   digest, because a design that CALLS a joblet has no definition of that
   joblet in its own evidence.
3. Then spawn ONE sub-agent per WORKER, all at once, each owning the SET of
   groups in its row. Do not spawn one per group.
4. The suggested split balances evidence bytes and caps how many groups sit in
   one worker's context; that cap is what stops a component belonging to one
   group being written into another group's analysis. You can see what the
   server cannot -- a group with CRITICAL flags may deserve a worker to itself.
   Deviate when you have a reason, keep whole groups together, and do not
   exceed the group-per-worker cap the plan states.
5. Record the split you actually used, and why, by appending to
   `<staging>/_knowledge/decisions.md`.

### What each worker does, per group, one group at a time

a. Read `<staging>/_assessment/quick_scan/{group}.md` -- THAT group's
   pre-computed risk flags and priority rank -- and calibrate depth
   accordingly. There is one such file per evidence file named in the work
   plan, so a split group has one per part. Do not read the full
   `_assessment/quick_scan.md` for this: it carries every group's flags and a
   worker needs only those of the groups it owns.
b. Read `<staging>/_preprocess/_analysis_guide.md` for analysis guidance and
   the section contract, and `<staging>/_analysis/_shared_library.md` if the
   pre-pass produced one -- that is where the definition of any reusable design
   this group calls lives.
c. Read `<staging>/_preprocess/{group}.md` for ALL scan data. If the group is
   split (`{group}_part1.md` and `{group}_part2.md` exist), read BOTH parts.
d. Analyse across ALL dimensions defined in the analysis guide.
e. Write ONE FILE PER GROUP: `<staging>/_analysis/{group}.md`, with one H2
   heading per section of the analysis guide. See "the shape of a group
   analysis" below.
f. Write a concise summary (max 50 lines) to
   `<staging>/_analysis/{group}_summary.md`: job count, key findings, top
   risks, critical expressions and rules discovered. Phase 3 agents use this
   summary to decide WHICH analyses to open. It is a navigation aid; it does
   not replace the analysis as the evidence of record.
g. Return a ~2KB navigation summary to the orchestrator (group name, job count,
   key findings).

ONE ANALYSIS FILE PER GROUP, ALWAYS. A worker covering four groups writes four
`{group}.md` files and four `{group}_summary.md` files. Never merge two groups
into one file, never widen a section to cover a group it is not named for, and
never let a group you own go unwritten because you ran out of room -- report it
instead. A split group is merged into ONE `{group}.md` covering both parts;
never one file per part.

The component names in `{group}.md` must come from THAT group's own evidence.
Holding four groups at once is exactly how a component that is real in one of
them gets written into another's analysis, and a later check compares each file
against its own group's evidence alone, so a sibling's evidence will not excuse
it.

### The shape of a group analysis

Use the section names from `_analysis_guide.md`, in the guide's order, each as
an H2 heading, spelled in CAPITALS exactly as the guide spells it:

```
# Analysis: {group}

## COMPONENTS
...
## SOURCE_SQL
...
```

The NAME is the contract. What goes under a heading is yours to choose --
tables, prose, code fences, any sub-heading shape that suits the evidence.

A SECTION YOU FOUND NOTHING FOR STILL GETS ITS HEADING, and a short honest
paragraph under it: what you examined and what was not there.

> ## SOURCE_SQL
> No SQL in this group: none of the 14 designs uses tDBInput, tDBRow or tELT*;
> the three tFileInput components read flat files.

That is a finding. It names the search, so a later reader can tell a real
absence from a search that asked the wrong question -- which is exactly the
distinction `confidence-model.md` draws between a `Confirmed` search and an
`Inferred` absence.

A heading followed by the single word "None" is NOT that. It records no search,
supports no grade, and is indistinguishable from a worker that never looked.
Neither is a missing heading: A MISSING HEADING MEANS NOBODY LOOKED, a short
honest one means somebody looked and there was nothing to find. Do not leave
the heading out, and do not pad it into a finding.

Be EXHAUSTIVE. Include every expression, every rule, every column. Depth over
brevity.

### Coverage, checked by you

NOT EVERY FILE IN `_analysis/` IS A GROUP ANALYSIS. Three kinds of file live
there and only one of them counts:

- `{group}.md` -- the group analysis. This is the only one that counts.
- `{group}_summary.md` -- that group's navigation aid. A summary is NOT
  evidence that the group was analysed, and a summary whose text says "see
  {group}.md" while no `{group}.md` exists is a MISSING group, not a covered
  one. Measured on a published run: the largest group in the estate, 98
  designs, shipped a summary pointing at an analysis that was never written.
- `_`-prefixed files -- run artifacts, never a group. `_shared_library.md` is
  the pre-pass digest; the rule is positional, so anything else written with a
  leading underscore is a run artifact too.

So `ls _analysis/*.md | wc -l` is NOT the number of groups analysed and must
never be used as one. Count `{group}.md` files whose name is on the group list,
and nothing else.

There is no Python gate counting your files. A check after the run does report
which groups have a summary and no analysis, but it reports after the fact: it
never writes the missing one, and by then the run is over. When the workers
return, check the work yourself against the group list in the run message and the
Groups table in `_assessment/phase2_work_plan.md`, per GROUP and never per
worker -- a worker that returned "done" having written three of its four groups
passes a per-worker check and fails this one.

For each group owed an analysis, confirm `_analysis/{group}.md` exists, is not
trivially small, and carries a heading for every section of the guide. A group
analysed by an earlier run may be stored as a `{group}/` directory of section
files instead; that counts as analysed, and you leave it alone.

Where a group is missing or thin, re-spawn a sub-agent for THAT group only --
not for the whole batch it came from, whose other groups are already written.
One retry per group. If a worker died mid-batch, its unwritten groups are
separate failures with separate retries, not one failure. After the retry, log
a WARNING naming every group and every section still missing, and carry on:
publishing what is good with the gap named beats publishing nothing.

## 5b. Phase 2.6 -- scorecard

The run message says whether the LLM scorecard is enabled.

When it is ENABLED, spawn ONE sub-agent to produce a structured assessment:
read every group's analysis; read `_scoring_rubric.md`, `_anti_patterns.md`,
`_red_flags.md`, `_security_checklist.md` and `_pattern_library.md`; score each
of the 8 dimensions 1-5 following the rubric criteria exactly, citing specific
evidence -- job names, component references, expressions, counts; identify
anti-patterns by AP-xxx-nnn ID and red flags by RF-xxx-nnn ID; complete the
security checklist; write the full scorecard to
`<staging>/_assessment/scorecard.md` in the format `_scoring_rubric.md`
defines; and return a ~2KB summary. A score without evidence is invalid. Be
specific: "7/12 DEU jobs lack tLogCatcher", not "error handling is weak".

When it is DISABLED, do not spawn a scoring sub-agent. The server has already
written `_assessment/scorecard.md` stating that no dimension scores were
computed. The consequence for Phase 3 is not optional: where a document would
cite a score, rating or ranking, state that scorecard-derived dimensions were
not computed for this run, and present the risk and quality findings directly
from the evidence you do have -- each group analysis's RISK_FLAGS and
ERROR_HANDLING sections, and `_assessment/quick_scan.md`. Do not invent a
number to fill the shape of a scorecard.

## 6. Phase 3 -- write the documents

Spawn the doc-assembly sub-agents in parallel, using the write model named in
the run message. Pass `model=` to each Task call.

| Agent         | Reference bundle      | Writes                                                                                                      |
|---------------|-----------------------|-------------------------------------------------------------------------------------------------------------|
| Core          | _ref_core*.md         | 00_AGENT_BRIEF, 01_ARCHITECTURE_MAP, 02_COMPONENT_INVENTORY, 03_DATA_FLOWS_AND_LINEAGE                      |
| Business+Ops  | _ref_business_ops*.md | 04_BUSINESS_RULES_AND_TRANSFORMATIONS, 05_RUNTIME_CONFIG_AND_EXTERNALS, 06_OPERATIONS_TESTING_AND_DEBUGGING |
| User          | _ref_user*.md         | 07_QUALITY_SECURITY_RISK_SCORECARD, 08_CHANGE_PLAYBOOK                                                      |

Three agents, and that is the whole table. Before spawning, read
`_preprocess/_doc_templates.md` for the exact required section structure of
every document; agents MUST follow those templates.

Each doc agent reads: the group analyses it needs
(`<staging>/_analysis/{group}.md`), `_analysis/{group}_summary.md` to decide
which ones those are, `_assessment/scorecard.md` for quantitative grounding,
`_assessment/quick_scan.md` for pre-computed risk flags, its own reference
bundles from `_preprocess/`, `_preprocess/_doc_templates.md`, and
`_preprocess/_phase3_guide.md` for cross-group synthesis.

THE SUMMARY IS A NAVIGATION AID, NOT THE EVIDENCE OF RECORD. The group analyses
are the evidence of record, and they are several times larger. OPEN the group
analysis whenever you need to check a claim, quote code, name a component, a
column, a context variable or a schedule, or whenever a group reads thin.
Writers who were kept away from the group analyses invented a SOAP namespace, a
token context variable and a component type that do not exist in the platform
-- while the real values sat in the analyses they were forbidden to open. If a
fact is not in front of you, go and read it.

Copy the four blocks in this section -- COVERAGE OBLIGATION, EVIDENCE
GROUNDING, CLIENT PROSE and UNRESOLVED EVIDENCE -- and the RUNTIME EVIDENCE
rules in section 4, VERBATIM into every doc sub-agent prompt. A writer that
never receives them pads, reconstructs quotes from memory, names internal files
in client prose, publishes a runtime percentage with no window or denominator
behind it, and has nowhere to put doubt. The boundary rules in section 2 apply
to every sub-agent too.

### COVERAGE OBLIGATION (there are no line targets -- coverage is measured)

PHASE 3 IS FOR THE READER. PHASE 2 WAS FOR THE RETRIEVAL CORPUS.
The group analyses behind you are exhaustive on purpose and nobody reads them
end to end. A published document IS read end to end, by a person, so selection,
narrative and structure are your job and they are your job only here. Choose
what matters, order it for the reader, and state plainly what you left out --
the omission rules below are how you do that. Leaving something out was never
the analyst's call upstream, and it is yours now.

The run message states how many scanned designs this project has, across how
many groups.

- WHAT THAT COUNT CONTAINS. It counts every scanned design, of every kind: the
  ones that run on their own AND the reusable ones that exist only to be called
  by another design. The per-kind breakdown for this estate is in
  `_assessment/phase2_work_plan.md`. Do not assume the total is all standalone
  jobs -- on a measured estate nearly a fifth of it was reusable.
- A reusable design is ACCOUNTED FOR by naming it and what calls it. It does
  not need the full standalone treatment a runnable design gets.
- Never pad a table with sub-components, steps or sub-jobs to make the numbers
  reach the total. A row must be a scanned design.
- If you judge a whole CLASS of artifact out of scope for the document you are
  writing, say so in the omission list -- "the 22 joblets are covered in <doc>
  rather than here" is a listed omission and is fine. Dropping them silently is
  not.
- A document that claims estate-wide scope -- 02_COMPONENT_INVENTORY above all,
  then 01_ARCHITECTURE_MAP and 03_DATA_FLOWS_AND_LINEAGE -- must ACCOUNT FOR
  EVERY scanned design in its scope. Accounted for means: named in a row of a
  table, or named in an explicit omission list. Nothing may be silently absent.
- Deliberate omissions are ACCEPTABLE and must be LISTED. Say which designs you
  left out and why (test harnesses, Copy_of_* duplicates, generated stubs,
  empty shells). An explicit, listed omission is fine. A silent one is a
  defect.
- Copy_of_* duplicates and test-named artifacts are the canonical examples: an
  estate commonly carries a handful of each, and they belong in the omission
  list by name, not in the inventory as if they were production designs and not
  silently dropped.
- Never write "all N designs" over a partial list. A published inventory
  claimed "All 142 jobs" and named 78 of them. Length was the measured thing,
  so a 55% coverage gap shipped unnoticed.
- Before you finish a document with estate-wide scope, count the designs you
  named and compare that to the design count for the groups in scope. If the
  numbers differ, add the missing rows or list them as omissions. State the
  count you actually named.
- Write as much as needed to cover every relevant finding. Do NOT pad.
- Do NOT truncate to hit a target. Include everything relevant.
- Depth comes from ACTUAL content (expressions, rules, schemas, risks), not
  filler.
- Every claim needs a specific job name and component reference.
- Show actual code/expressions/SQL, then explain. Never just summarize.

### DEPTH FLOOR -- A SHORT DOCUMENT MEANS EVIDENCE WAS MISSED

The run message carries a per-document floor in lines, scaled to this estate
and graded after the run against those same numbers.

Read the floor as a DETECTOR, not as a quota. It is roughly what covering this
estate honestly costs, so a document that lands under its floor is telling you
that designs, expressions, columns, schemas or risks were left out: go back to
the evidence and put them in. It is NEVER a reason to stretch prose. Padding to
reach a floor is the worse failure of the two, because filler is
indistinguishable from content to the reader and it buries the findings that
are real.

A floor is not a ceiling. A document with more to say says more. Depth is
enumerated evidence and nothing else: Do NOT pad, and Do NOT truncate to hit a
target. Measured: one published run wrote a 76-line project brief for a
20-group estate because nothing in its instructions said what covering it
costs.

### EVIDENCE GROUNDING -- COPY, NEVER RECONSTRUCT

The rule and its measured failure are in section 3. Restated for the writer in
one line, because it is the one a writer breaks: if you did not copy it, do not
quote it.

### CLIENT PROSE -- NAME THE ESTATE, NEVER THE MACHINERY

- The published documents name designs, components, jobs, tables, systems and
  business objects. They NEVER name the machinery that produced them.
- PROHIBITED anywhere in a published .md document: staging or other internal
  directory paths; the names of internal working files (_analysis, _assessment,
  _preprocess, _ref, _provenance, quick_scan, docs_staging, scan_result.json);
  and any phrase of the form "based on our internal analysis", "per the
  analysis in ...", "the analysis file states", or a reference to a phase, a
  sub-agent, a script or these instructions.
- Cite evidence the way an engineer would, in the prose itself: name the DESIGN
  and the COMPONENT ("OrderLoad_Daily, tMSSqlOutput_1"), never the file you
  read it from.
- Machine-checkable provenance goes in a sidecar the reader never sees. Write
  exactly one JSON file per document you produce, to
  `<staging>/_provenance/{DOC_NAME}.json` (DOC_NAME is the document's filename
  without .md), with this shape:

```
{
  "document": "02_COMPONENT_INVENTORY",
  "entries": [
    {
      "claim": "<the quoted block or protected fact, first 200 chars>",
      "group": "<group name>",
      "unit": "<design or job name, or null for a cross-group fact>",
      "components": ["tMSSqlOutput_1", "tMap_3"],
      "source": "_analysis/<group>.md",
      "verbatim": true
    }
  ]
}
```

- One entry per quoted code block, expression, SQL statement, configuration
  value and protected fact (counts, schedules, environments, coverage numbers).
  A quoted block with no sidecar entry counts as unsourced.
- "components" lists the component instances the claim rests on, copied from
  the evidence for that "group" -- the ones you actually read to make the
  claim, not every name the sentence mentions. Use [] when the claim rests on
  no single component (a count, a group-wide statement). This is the one field
  checked mechanically: each name is looked up in that group's own evidence, so
  a name that is not in it is reported against you by name, and naming a
  component you did not read is worse than naming none.

### UNRESOLVED EVIDENCE

As section 3 defines it. Every doc agent appends to the same ledger with the
same heredoc.

### Scorecard integration

- Reference specific scores in your documents, and name the published document
  that carries them, never an internal file. Example: "Security: 2/5 (see
  07_QUALITY_SECURITY_RISK_SCORECARD)".
- Include anti-pattern IDs and red flag IDs where relevant.
- If the scores were not computed for this run, say so where a score would go.
  Do not invent a number to fill the shape of a scorecard.
- 07_QUALITY_SECURITY_RISK_SCORECARD MUST include scores, confidence, evidence,
  red flags, quick wins and unknowns.
- 08_CHANGE_PLAYBOOK MUST include inspect/test/rollback guidance.
- 00_AGENT_BRIEF MUST include onboarding and documentation-map guidance.

### Narrative rules

- Never summarize when you can enumerate
- Every claim needs a job name + component reference
- Show ACTUAL code/expressions/SQL, then explain
- "Error handling is weak" is NOT acceptable
- "7 of 12 DEU jobs have no tLogCatcher" IS acceptable
- Include concrete counts, percentages, and job names
- Cross-reference data from multiple groups to build comprehensive docs
- Match the depth and structure shown in the reference doc templates

### Document catalog

Always generate exactly the 9 assistant-grade docs, 00_AGENT_BRIEF through
08_CHANGE_PLAYBOOK. Do not generate an executive summary, a standalone FAQ, a
migration assessment, or split 05a-05d docs.

Quality standards: every doc has a table of contents; plain ASCII tables, no
Unicode box-drawing; business rules tagged BR-{GROUP}-NNN with full
expressions; risk items graded CRITICAL / HIGH / MEDIUM / LOW; job names,
component refs and actual code for traceability; schema docs with column-level
detail (names, types, nullable); data-flow docs with transformation expressions
and explanations.

### Checking the documents

When the doc agents return, list `<staging>/*.md`. All nine documents must
exist and be over 500 bytes. If one is missing or under that, identify which
agent owned it and re-spawn ONLY that agent, once. Then run Section A of
`_method/_shared/validation-checklist.md` over what you have. Proceed to
Phase 4 with whatever exists, logging anything still missing as a WARNING.

## 7. Phase 4 -- publish

- Do NOT delete `<staging>/_analysis/` or `<staging>/_preprocess/`. The server
  removes both after the run, once the deterministic checks that read them have
  run. Deleting your own evidence before it can be checked is how a check stops
  existing. Neither directory is published: a leading underscore keeps it out
  of the client corpus and out of the document count.
- KEEP `<staging>/_assessment/scorecard.md` and
  `<staging>/_assessment/quick_scan.md` -- both are part of the final output.
- KEEP `<staging>/_provenance/` -- the per-document provenance sidecars. They
  are checked after the run and are never shown to the reader.
- The open-questions ledger stays in `<staging>/_knowledge/`. It is already
  behind an underscore, so nothing needs moving and nothing needs a cleanup
  step that could lose it.
- Write `<staging>/index.json` mapping docs to groups, including every produced
  doc with its filename exactly as written.
- Write the run log to the logs directory named in the run message.
- Do NOT push to RAG. The server does that after a successful run.
