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
1. `03_DATA_FLOWS_AND_LINEAGE.md` -- the primary source. Its flow sections are the
   directional business narrative you are converting into data.
2. `01_ARCHITECTURE_MAP.md`, `04_BUSINESS_RULES_AND_TRANSFORMATIONS.md`,
   `05_RUNTIME_CONFIG_AND_EXTERNALS.md` -- system roles, transformations, external endpoints.
3. `systems.jsonl`, `applications.jsonl`, `edges.jsonl`, `connections.jsonl` -- the scanner
   landscape. USE THE SPELLING OF SYSTEM NAMES FOUND HERE whenever a document refers to the
   same system, so the two views join.
4. `artifacts_min.jsonl` -- the designs (jobs/routes) that exist in this sub-project. Every
   name you put in `implementing_designs` should be a `name` or `job_id` from this file,
   copied exactly.
5. `technical_facts_min.jsonl` -- protocols, endpoints, queues/topics, components.

RULES
- One entry in `flows` per (direction, business object). `Maximo -> 4PS WorkOrder` and
  `4PS -> Maximo WorkOrder` are two flows. Do not merge them into one bidirectional edge,
  and do not split one flow into per-job entries.
- `source_system` / `target_system` are SYSTEMS an organisation would recognise: Maximo, 4PS,
  SharePoint, a named database, a message broker. Never a filename, folder, component type,
  endpoint, queue name, Talend job, or source root. If a document does not name the system on
  one end, omit the flow and record it under `excluded` -- do not write "unclear" as a name.
- `business_object` is the thing being exchanged (WorkOrder, Invoice, Location, Vendor), one
  stable singular noun. Not a table, queue, or job name.
- `implementing_designs` lists the designs that implement the flow, exact names. This is what
  gives the published topology its drill-down to real code, so be complete and be precise:
  `AssetCheck_GetLocation` and `AssetCheck_GetLocations` are different jobs.
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
- `evidence` names the document and section you took the flow from, e.g.
  `03_DATA_FLOWS_AND_LINEAGE.md - 'Maximo-to-AssetCheck Flows (REST/File Ingestion)'`.
  Copy section headings as they appear.
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
      "name": "Maximo",
      "role": "Enterprise Asset Management system; source of work orders and locations",
      "kind": "application | database | external_service | file | queue",
      "aliases": ["MX"]
    }
  ],
  "flows": [
    {
      "name": "Maximo -> AssetCheck: Work Order ingestion",
      "source_system": "Maximo",
      "target_system": "AssetCheck",
      "business_object": "WorkOrder",
      "carries": "What is exchanged and what happens to it, in business terms.",
      "mechanism": "REST (ingest) + File staging + REST (serve)",
      "requiredness": "required | optional | conditional | not_established",
      "implementing_designs": ["Maximo2AssetCheck_MainJob", "AssetCheck_GetWorkOrder"],
      "technical_path": [
        {
          "name": "Receive Maximo WorkOrder webhook",
          "mechanism": "REST webhook",
          "protocol": "REST",
          "mode": "event_driven",
          "component": "maximoRestService",
          "resource": "Maximo WorkOrder XML",
          "implementing_designs": ["maximoRestService"]
        }
      ],
      "transformations": [
        {
          "name": "WorkOrder status mapping",
          "description": "Status translation applied on the way to the target.",
          "implementing_designs": ["workorder_to_fourps"],
          "mappings": [
            {"source": "STATUS=VOORBEREID", "target": "In Process", "mapping_type": "value"}
          ]
        }
      ],
      "confidence": "high",
      "evidence": "03_DATA_FLOWS_AND_LINEAGE.md - 'Maximo-to-AssetCheck Flows (REST/File Ingestion)'"
    }
  ],
  "excluded": [
    {"name": "MockApi, testjobs", "reason": "test scaffolding, not a production flow"}
  ]
}
```
