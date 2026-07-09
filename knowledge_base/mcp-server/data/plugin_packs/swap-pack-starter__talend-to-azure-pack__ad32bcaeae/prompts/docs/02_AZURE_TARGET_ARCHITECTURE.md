Context And Assumptions:
- Restate the migration scope: which Talend jobs are covered by this bundle.
- List the assumptions the generated artifacts depend on (target
  subscription layout, network reachability of evidenced sources, existing
  databases staying in place unless evidence says otherwise).

Target Architecture:
- Describe the target Azure stack. Default mapping, unless evidence forces
  a different choice: Azure Data Factory for pipelines and orchestration,
  Mapping Data Flows for row-level transformation, Azure Key Vault for
  secrets, Azure Blob Storage or ADLS Gen2 for file landing zones,
  self-hosted integration runtime for on-premises sources, Azure Monitor
  plus ADF diagnostics for logging and alerting.
- Include a simple ASCII flow diagram from sources through ADF to targets.

Component Mapping:
- Table mapping each evidenced Talend construct to its Azure equivalent:
  job to pipeline, tMap to data flow or SQL, tRunJob to Execute Pipeline,
  context group to global parameters plus Key Vault, TAC schedule to
  trigger, tLogCatcher to ADF monitoring and alerts.
- For CUSTOM-classified jobs, state the chosen landing (Azure Function,
  Databricks notebook, or stored procedure) and why.

Key Design Decisions:
- Present each significant decision with the option chosen, the main
  alternative, and pros and cons in two or three lines each. Cover at
  least: data flow versus SQL pushdown, trigger strategy, integration
  runtime placement, and error-handling pattern.

Risks And Open Questions:
- Pull the high-complexity and low-evidence items from the scorecard and
  state what could break and what must be answered before cutover.

Evidence Notes:
- Name evidence gaps that shaped or limited the architecture choices.
