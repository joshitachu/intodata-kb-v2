You are extracting the integration architecture of one sub-project as structured data.

You have just finished writing this sub-project's documentation. Read your own finished
documents and state, in business terms, WHICH SYSTEMS EXCHANGE WHAT WITH WHOM.

Your only output is one JSON object matching the contract below. No Markdown fences, no
preamble, no explanation.

WHAT YOU DO NOT HAVE TO DO
You do not invent identifiers, status values, confidence numbers, artifact hashes, or
coverage figures. Downstream code derives all of those. Name things the way a solution
architect names them and refer to designs by their plain names. Getting this contract
right is enough; there is no hidden bookkeeping to satisfy.

YOUR EVIDENCE (attached as context files, no tool calls needed)
1. The documents you just wrote, attached in priority order:
${DOCUMENTS}
   These are your primary source. Whichever of them describes data flows carries the
   directional business narrative you are converting into data; the others supply system
   roles, transformations and external endpoints.
2. `systems.jsonl`, `applications.jsonl`, `edges.jsonl`, `connections.jsonl` -- the scanner
   landscape. USE THE SPELLING OF SYSTEM NAMES FOUND HERE whenever a document refers to the
   same system, so the two views join.
3. `artifacts_min.jsonl` -- the designs that exist in this sub-project, whatever the platform
   calls them (jobs, routes, pipelines, recipes, packages). Every name you put in
   `implementing_designs` should be a `name` or `job_id` from this file, copied exactly.
4. `technical_facts_min.jsonl` -- protocols, endpoints, queues/topics, components.

RULES
- One entry in `flows` per (direction, business object). A flow carrying orders from A to B
  and one carrying them back from B to A are two flows. Do not merge them into one
  bidirectional edge, and do not split one flow into per-design entries.
- `source_system` / `target_system` are SYSTEMS an organisation would recognise by name: an
  ERP, a CRM, an asset-management system, a named database, a message broker, a SaaS service.
  Never a filename, folder, component type, endpoint, queue name, individual design/job, or
  source root. If a document does not name the system on one end, omit the flow and record it
  under `excluded` -- do not write "unclear" as a name.
- `business_object` is the thing being exchanged (an order, an invoice, a location, a vendor),
  one stable singular noun in the source vocabulary. Not a table, queue, or design name.
- `implementing_designs` lists the designs that implement the flow, exact names. This is what
  gives the published topology its drill-down to real code, so be complete and be precise:
  two designs whose names differ by one character are two different designs, never one.
- `technical_path` is the ordered hand-off sequence from source to destination, one step per
  real hop (receive -> queue -> transform -> write). Include it whenever your documents
  establish the order. Put queues/topics/files in `resource` and job/endpoint names in
  `component`. Never invent a hop to make a path look complete; omit `technical_path` if the
  order is not established.
- `transformations` records real field-level mapping you documented, with `mappings` entries.
  Omit the key when you documented none.
- `confidence` is one of `certain`, `high`, `medium`, `low` -- your own assessment of how
  firmly your documents establish the flow. Use `low` freely; a low-confidence real flow is
  more useful than a missing one, and nothing is published as fact on your word alone.
- `evidence` names the document and the section heading you took the flow from, in the form
  `<DOCUMENT>.md - '<Section heading>'`. Copy both exactly as they appear above; the citation
  is resolved by matching them.
- `excluded` is where test jobs, playground/scaffolding designs, and flows you could not
  resolve go, each with a short `reason`. State what you left out.
- Prefer a smaller set of well-established flows over a hairball of guesses, but do not omit
  a flow your documents clearly describe merely because the scanner landscape has no edge
  for it -- that gap is expected and is handled downstream.
- Do not reproduce secrets, credentials, tokens, complete sensitive URLs, or private keys.

THE BUSINESS LAYER (record only what your documents state; omit a key rather than guess)
Every item below is optional. A missing item costs nothing; an invented one misleads the
people who will act on it. Each item that takes `evidence` cites the document and section it
came from in the same `<DOCUMENT>.md - '<Section heading>'` form as a flow.
- Per flow, structured facts that must NOT be folded into `carries`:
  * `mechanism`: the transport chain in a few words (it is published as its own field).
  * `generation`: which platform generation or era the documents place the flow in.
  * `key_form`: the business key the exchange is matched on (e.g. "order number + line").
  * `verdict`: the short assessment the documents state, e.g. keep / replace / duplicate /
    obsolete, with a few words of reason. Never your own recommendation.
  * `conditions`: whether, when and in what order the exchange happens -- one entry per
    precondition, with `kind` one of `trigger`, `filter`, `ordering`, `schedule`,
    `disabled`, `guard`, `unknown`. A value translation is NOT a condition; it is a
    transformation.
- `transformations[].kind` is one of `field_mapping` (fields carried to fields),
  `value_domain` (the accepted values of one target field -- name it in `target_field`),
  `derived_rule` (a computed value), `constant_set` (fixed values written). Each mapping may
  add `expression` (the verbatim rule when it differs from `source`), `category` (what kind of
  field, e.g. identifier / status / date), `reaches_target` (`yes`, `no`, `partial`,
  `disabled`, `unknown` -- whether the value actually arrives) and a short `note`.
- `business_rules` (per flow): decisions the integration takes on the business's behalf --
  which record wins, how a status is chosen, what is derived, what is silently dropped.
  `kind` is one of `value_mapping`, `precedence`, `derivation`, `selection`, `feedback`,
  `ordering`, `disabled`, `threshold`, `unknown`. `statement` is one sentence a business owner
  can confirm or reject; `inputs` / `output` name the fields involved; `owner` only when a
  document names the owning team or role; `owner_question` is the question to put to that
  owner. `transformation` may name the transformation of the same flow that implements it.
- Top-level `controls` (validation, monitoring, filtering, authentication, reconciliation),
  `risks` (with `severity` one of `low`, `medium`, `high`, `critical`, `unknown`),
  `open_questions` (what the documents explicitly leave unresolved, phrased as a question) and
  `conflicts` (two accounts of a flow that disagree; `conflict_type` one of
  `runtime_vs_implementation`, `supporting_vs_implementation`, `supporting_sources_disagree`,
  `unresolved`). Each lists in `flows` the exact `name` of the flows above it concerns, or
  `[]` when it concerns the whole landscape.
- `systems[].owner`: the owning team, department or role, only when a document states it.
  Never a personal e-mail address or phone number.
- Rules, controls and risks may carry their own `confidence` word; otherwise the flow's
  applies. When the documents do not establish a fact, leave it out or ask it as an
  `open_questions` entry -- "not established" is a correct answer, a guess is not.

SUB-PROJECT: ${SUBPROJECT}

CONTRACT
```
{
  "subproject": "${SUBPROJECT}",
  "systems": [
    {
      "name": "<SystemName>",
      "role": "What this system is and what it contributes to the flows",
      "kind": "application | database | external_service | file | queue",
      "aliases": ["<other spelling used in the docs>"],
      "owner": "<owning team or role, only when documented>"
    }
  ],
  "flows": [
    {
      "name": "<Source> -> <Target>: <Business object> <verb>",
      "source_system": "<SystemName>",
      "target_system": "<SystemName>",
      "business_object": "<BusinessObject>",
      "carries": "What is exchanged and what happens to it, in business terms.",
      "mechanism": "REST (ingest) + File staging + REST (serve)",
      "requiredness": "required | optional | conditional | not_established",
      "generation": "<platform generation the documents place it in>",
      "key_form": "<business key the exchange matches on>",
      "verdict": "<assessment the documents state, e.g. duplicate of another flow>",
      "conditions": [
        {"kind": "trigger", "statement": "Runs when the order is released.", "evidence": "<DOCUMENT>.md - '<Section heading>'"}
      ],
      "implementing_designs": ["<exact design name>", "<exact design name>"],
      "technical_path": [
        {
          "name": "<what this hop does>",
          "mechanism": "REST webhook",
          "protocol": "REST",
          "mode": "event_driven",
          "component": "<endpoint or design name>",
          "resource": "<queue, topic, file or payload>",
          "implementing_designs": ["<exact design name>"]
        }
      ],
      "transformations": [
        {
          "name": "<what is transformed>",
          "description": "Status translation applied on the way to the target.",
          "kind": "field_mapping | value_domain | derived_rule | constant_set",
          "target_field": "<target field whose values a value_domain defines>",
          "implementing_designs": ["<exact design name>"],
          "mappings": [
            {"source": "<source field or value>", "target": "<target field or value>", "mapping_type": "value", "expression": "<verbatim rule>", "category": "status", "reaches_target": "yes", "note": ""}
          ]
        }
      ],
      "business_rules": [
        {
          "name": "<short rule name>",
          "kind": "precedence",
          "statement": "When both sources supply a date, the later one wins.",
          "inputs": ["<source field>", "<source field>"],
          "output": "<target field>",
          "owner": "<owning team or role, only when documented>",
          "owner_question": "Is the later date still the correct choice?",
          "transformation": "<name of the transformation above, if any>",
          "evidence": "<DOCUMENT>.md - '<Section heading>'"
        }
      ],
      "confidence": "high",
      "evidence": "<DOCUMENT>.md - '<Section heading>'"
    }
  ],
  "excluded": [
    {"name": "<designs you left out>", "reason": "test scaffolding, not a production flow"}
  ],
  "controls": [
    {"name": "<control>", "description": "Incoming messages are validated against a schema.", "control_type": "validation", "flows": ["<exact flow name>"], "evidence": "<DOCUMENT>.md - '<Section heading>'"}
  ],
  "risks": [
    {"name": "<risk>", "description": "Rejected records are not reported to anyone.", "severity": "high", "flows": ["<exact flow name>"], "evidence": "<DOCUMENT>.md - '<Section heading>'"}
  ],
  "open_questions": [
    {"question": "Who consumes the file written to the outbound folder?", "flows": ["<exact flow name>"], "evidence": "<DOCUMENT>.md - '<Section heading>'"}
  ],
  "conflicts": [
    {"description": "The design is documented as disabled, but runtime records show it running.", "conflict_type": "runtime_vs_implementation", "flows": ["<exact flow name>"], "evidence": "<DOCUMENT>.md - '<Section heading>'"}
  ]
}
```
