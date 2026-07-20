Generate `{doc_name}.md` as part of a Talend reverse-engineering deliverable
for a client rationalisation programme.

HARD RULE: each generated `01_INTERFACE_TEMPLATE__<group>__<job>` document must
reproduce the client reverse-engineering template structure, filled from
evidence for exactly one scanned job. Follow that document's instructions
exactly — do not substitute a different structure or merge jobs.

Write for a technical and business audience reviewing whether and how to
re-platform these integrations. Use direct language and stable headings. Prefer
specific names (jobs, components, endpoints, context variables) over generic
descriptions.

Evidence-marking rules (these are the core of this deliverable and override
style preferences):

- Mark every material claim as one of:
  - **[FACT]** — directly observed in the Talend source. Give a source
    reference in the form `(job_file, component_unique_name)` or
    `(context_group_item, variable)`.
  - **[ASSUMPTION]** — an inference. State the reasoning and a confidence
    (HIGH/MEDIUM/LOW). It must be clearly an inference, not presented as fact.
  - **[VALIDATION QUESTION]** — something not derivable from the source. Phrase
    it as a question and name the audience (business / application management /
    integration / architecture).
- When evidence for a required topic is absent, write the word **UNKNOWN**
  followed by a validation question. Never fabricate systems, fields, queries,
  schedules, credentials, owners, SLAs, volumes, or runtime behaviour to fill a
  gap.
- Quote SQL, tMap expressions, and custom Java verbatim in fenced code blocks;
  these are the reference the client will check against.
- Never reproduce secret values. Name the credential variable and its type and
  say the value is redacted / encrypted / stored in cleartext (a cleartext
  secret is itself a finding — record it as a risk).
- Runtime dimensions (throughput, job duration constraints, historical error
  rates, real schedules, monitoring thresholds) are not present in a Talend
  export. Do not estimate them; record them as validation questions.

General rules:

- Use only the supplied analysis, evidence bundles, facts, scorecard data, and
  examples. Do not copy example content; use examples for structure and tone.
- Keep uncertainty explicit and traceable. If evidence is missing, name the
  missing evidence and the affected interface.
- End every document that requires it with an Evidence Register table:
  columns Topic | Interface / Scope | Source (file, component) | Confidence.
