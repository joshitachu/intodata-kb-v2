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
      "aliases": ["<other spelling used in the docs>"]
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
          "implementing_designs": ["<exact design name>"],
          "mappings": [
            {"source": "<source field or value>", "target": "<target field or value>", "mapping_type": "value"}
          ]
        }
      ],
      "confidence": "high",
      "evidence": "<DOCUMENT>.md - '<Section heading>'"
    }
  ],
  "excluded": [
    {"name": "<designs you left out>", "reason": "test scaffolding, not a production flow"}
  ]
}
```
