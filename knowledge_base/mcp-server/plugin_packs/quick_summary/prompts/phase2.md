You are generating a Phase 2 group analysis to support a short executive summary for a
non-technical business stakeholder. Your goal is NOT exhaustive technical enumeration -- it is
to capture what matters to the business: what the integrations do, where data moves, and where
the risks are.

Focus on:
- What each integration or unit does in plain business terms (what business process it serves)
- Source and target systems for every data flow (database names, file paths, API endpoints)
- Business rules and transformations that are business-critical or fragile
- External system dependencies and any hardcoded credentials or connection details
- Concrete operational risks: what breaks, what has no error handling, what has no monitoring
- Any red flags that a business owner should know about

Skip:
- Exhaustive component-by-component enumeration of low-level connector details
- Full verbatim expression dumps for every tMap column (show only expressions that encode real business logic)
- Technical scaffolding details (tLogRow, tBufferInput, internal flow routing) unless they represent a risk

Still required in GROUP_SUMMARY: a concise list of integrations, their source/target systems,
and the top 3-5 risks with severity and recommended owner. This summary feeds directly into the
executive document.
