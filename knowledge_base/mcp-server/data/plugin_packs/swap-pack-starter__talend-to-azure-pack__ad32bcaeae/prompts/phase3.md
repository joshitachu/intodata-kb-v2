Generate `{doc_name}.md` as part of a Talend-to-Azure migration bundle.

Write for a data engineer who will deploy this bundle into an Azure
environment and decommission the Talend jobs it replaces. Use direct
language and stable headings. Prefer specific names over generic
descriptions.

Deployable-artifact rules (these override style preferences):

- When the document instructions ask for Azure Data Factory JSON, Bicep,
  parameter files, or scripts, emit complete artifacts in fenced code blocks,
  each preceded by a bold filename line such as `**File: pipelines/PL_Load_Customers.json**`.
  The reader must be able to save each block under that filename and deploy
  without editing the artifact body.
- Never place secrets, passwords, or connection strings in artifacts. Use
  ADF Key Vault references and template parameters. Secret names must follow
  the pattern `kv-<connection-name>-<field>` derived from the evidenced
  Talend connection or context variable name.
- Map every evidenced Talend context variable to an ADF global parameter or
  Bicep parameter with the same name, so the Talend-to-Azure correspondence
  stays traceable.
- Name generated resources consistently: pipelines `PL_<JobName>`, datasets
  `DS_<System>_<Object>`, linked services `LS_<System>_<ConnectionName>`,
  data flows `DF_<JobName>`, triggers `TR_<JobName>`.
- Every generated artifact must trace to an evidenced Talend job or
  connection. If a job cannot be fully translated (custom Java, missing
  evidence), still generate the surrounding pipeline and insert a clearly
  marked placeholder activity whose description names the Talend component
  and the reason, and list it under Evidence Notes. Do not silently drop
  logic and do not invent logic to fill the hole.

General rules:

- Use only supplied analysis, evidence bundles, facts, scorecard data, and
  examples.
- Do not invent systems, fields, routes, tables, queries, schedules,
  credentials, owners, SLAs, or runtime behavior.
- Keep uncertainty explicit. If evidence is missing, name the missing
  evidence and the affected job.
- Include an Evidence Notes section when required by the document
  instructions.
- Do not copy example content; use examples only for structure and tone.
