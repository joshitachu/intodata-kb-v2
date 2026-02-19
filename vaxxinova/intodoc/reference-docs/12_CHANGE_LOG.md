# Change Log

This document is intended to track changes to the Vaxxinova Talend integration platform -- modifications to jobs, configuration updates, schema changes, new region onboarding, and any other alterations that affect the behavior of the 257-job ETL pipeline. A well-maintained change log is essential for debugging production issues, coordinating cross-regional deployments, and providing an audit trail for data lineage and compliance requirements.

---

## Current State: No Change History Available

The Vaxxinova Talend platform does not currently maintain a structured change log. The static code analysis that produced this documentation suite was performed against a point-in-time snapshot of the codebase and cannot reconstruct the history of changes made over the platform's lifetime. This is a significant operational gap: when a production issue occurs, there is no quick way to determine what changed recently, who changed it, or how to roll it back.

Several indirect indicators of change history exist in the codebase, but none constitute a reliable record:

### Version Numbers in Job Names

Jobs like `VIT_Clean_Sales_Set_v6`, `Salesforce_Budget_v8`, and `NPL_Clean_Item_v26` encode version history in their names. Version 26 of NPL_Clean_Item implies at least 25 prior iterations, but the nature of the changes between versions, who made them, and when they were deployed is not recorded anywhere accessible to static analysis. High version numbers indicate active maintenance but provide no detail about what changed.

The version numbering convention is also inconsistent. Some jobs use `_v1`, `_v2` suffixes; others use `_0.1`, `_1.0` semantic versioning (e.g., `Salesforce_Products`); still others have no version indicator at all. Without a standardized versioning scheme, it is impossible to infer the relative maturity or currency of a job from its name alone.

### Backup and Copy Variants

The presence of jobs named `VBR_Clean_Build_Sales_Set_Backup`, `Copy_of_VBR_Clean_Build_Sales_Set`, `VBR_Clean_Ingest_Sales_Set_DEBUG`, and `VBR_Clean_Ingest_Sales_Set_Backup_2` suggests a development workflow where developers copy a job before modifying it, keeping the original as a rollback target. This is a manual form of version control, but it creates ambiguity about which variant is canonical and does not capture the rationale for changes.

Across the platform, approximately 40 jobs are identified as copies, backups, or debug variants. These represent a shadow change history: each copy is a snapshot of a job at a point in time, but without dates, commit messages, or authorship information, they serve only as rollback targets, not as documentation of change.

### Git History

If the Talend project is stored in a Git repository (as the project structure suggests), the commit log would provide the most complete change history available. However, Talend's XML-based project files produce large, difficult-to-read diffs that obscure the functional intent of changes. A commit that modifies a tMap component's mapping expression generates hundreds of lines of XML diff, none of which clearly communicates "added the Fiscal_Year column to the Sales_Set output."

Git history is also subject to practices that reduce its usefulness: large batch commits ("updated all BRA jobs"), commits without messages, and commits that mix functional changes with formatting or metadata changes. Without commit message discipline, Git history becomes a record of when things changed but not why.

### Talend Administration Center (TAC) Logs

If the platform uses Talend Administration Center for scheduling and deployment, TAC maintains execution logs that record which version of which job was deployed and when. However, TAC logs are operational records (job X ran at time T with exit code 0) rather than change records (job X was modified to add column Y). They are useful for incident investigation but do not constitute a change log.

---

## Recommendation: Implement Change Logging in the Migration Target

The migration to a new integration platform presents an opportunity to establish change tracking as a first-class practice rather than an afterthought. The following recommendations apply regardless of the target platform (Airflow, dbt, Azure Data Factory, custom Python pipelines, or any other integration framework).

### Integrate with Version Control

Every change to pipeline logic, configuration, or schema should be committed to a version control system (Git) with a meaningful commit message. The target platform should store pipeline definitions in a text-friendly format (YAML, JSON, Python, SQL) rather than binary or complex XML, making diffs readable and reviewable. Pull request descriptions should serve as the primary narrative record of what changed and why.

A code review policy should require that pull requests for pipeline changes include:

- A description of the business reason for the change
- A list of affected jobs or pipelines
- A summary of testing performed
- A rollback plan if applicable

### Adopt a Change Log Standard

Maintain a change log that records the business intent of each change, not just the technical diff. The log should answer five questions for every entry:

1. **What** changed? (Which jobs, configurations, or schemas were modified?)
2. **Why** did it change? (What business requirement, bug fix, or improvement motivated the change?)
3. **Who** made the change? (Which team member or team is responsible?)
4. **What is affected?** (Which downstream jobs, reports, or systems depend on the changed component?)
5. **How to roll back?** (What steps reverse the change if it causes problems?)

The change log should be maintained in a structured format (Markdown, as shown in the template below) and stored alongside the pipeline code in version control. It should not live in a wiki, a shared drive, or a ticketing system where it may become separated from the code it describes.

### Automate Where Possible

The change log should not rely entirely on manual discipline. Several automation opportunities exist:

- **CI/CD pipelines** can auto-generate entries from pull request titles and descriptions when a merge to the main branch occurs.
- **Database migration tools** (Alembic, Flyway, Liquibase) maintain their own change logs for schema changes. These should be referenced from the main change log.
- **Deployment automation** can record which version of which pipeline was deployed to which environment and when, creating an auditable deployment history.
- **Monitoring and alerting systems** can append entries when production incidents are detected and resolved, linking the incident to the change that caused it.

### Require Rollback Plans

Every change to a production pipeline should include a documented rollback plan. For ETL pipelines, rollback is not always as simple as reverting code -- it may require reprocessing data, restoring database snapshots, or re-running upstream jobs. The rollback plan should be specific enough that an on-call engineer unfamiliar with the change can execute it under time pressure.

Common rollback scenarios for this platform include:

- **Schema changes**: Revert the pipeline code and re-run the job with the old schema. If the target table has already been altered, a schema migration rollback may also be needed.
- **Data flow logic changes**: Revert the pipeline code, delete the output of the changed job (using the DELETE-then-INSERT pattern already common in the platform), and re-run with the old logic.
- **Configuration changes**: Restore the previous environment variable values and restart affected jobs.
- **Credential changes**: Restore previous credentials from the secrets vault (which should maintain version history) and restart affected jobs.

---

## Change Log Template

The following template should be used for all future change log entries. Each entry captures the essential information needed for operational awareness, debugging, and audit compliance.

```markdown
## [YYYY-MM-DD] -- Brief Title of Change

**Author:** [Name or team]
**Ticket:** [JIRA/issue tracker reference, if applicable]
**Environment:** [PROD / TEST / both]
**Deployment date:** [Date deployed to production, if different from change date]

### Summary

[1-3 sentences describing what changed and why. Focus on business intent, not
technical detail. Example: "Added currency conversion for South African Rand
(ZAR) to support the new VSA subsidiary onboarding. Without this change, VSA
sales data would appear in ZAR in global reports that expect USD."]

### Affected Jobs

| Job Name | Region | Change Type |
|----------|--------|-------------|
| [job_name] | [region] | [added / modified / deleted / config change] |

### Technical Details

[Optional. Include if the change involves non-obvious logic: new join
conditions, modified SQL queries, changed API endpoints, updated context
variable values, new columns in output schemas, etc.]

### Testing

[How was this change validated? Which environments were tested? Were
regression tests run against downstream jobs? Were row counts compared
between old and new outputs?]

### Rollback Plan

[Specific steps to reverse this change if it causes production issues.
Include:
- Which jobs to revert or redeploy
- Whether data needs to be reprocessed
- Whether database rollback (DELETE/re-ingest) is required
- Estimated time to execute rollback
- Who to contact if rollback requires manual intervention]

### Dependencies

[Other changes that must be deployed alongside this one, or changes that
depend on this one being in place first. Example: "Requires the ZAR
conversion rate entry in Global_Budget.Budget_Current_Conversion_Rate to
be populated before this job runs."]
```

---

## Guidelines for Maintaining the Change Log

The following guidelines are intended to ensure the change log remains useful over time. A change log that is incomplete, outdated, or inconsistent is worse than no change log at all, because it creates a false sense of documentation coverage.

### Record Changes Contemporaneously

Record changes at the time they are made, not after the fact. A change log that is updated days or weeks later is unreliable -- details are forgotten, affected jobs are omitted, and rollback plans are written in hindsight rather than in advance. Integrating the log into the pull request or deployment workflow ensures entries are created contemporaneously with the change.

A practical approach is to require that the change log entry be written as part of the pull request. The reviewer checks the entry for completeness alongside the code changes. The entry is merged into the change log file as part of the same commit that introduces the change.

### One Entry Per Logical Change

Create one entry per logical change, not per file modified. A change that updates three jobs to add a new column should be a single entry describing the column addition, listing all three jobs in the Affected Jobs table. Do not create three separate entries for what is conceptually one change.

Conversely, if a pull request contains two unrelated changes (a bug fix and a new feature), consider splitting it into two entries. The change log should reflect business-level changes, not version control operations.

### Include Configuration Changes

Updates to context variables (database endpoints, API credentials, BigQuery dataset names) are often the most operationally impactful changes but the easiest to overlook. A change to a PROD connection string is at least as important as a change to job logic, because it affects every job that uses that connection. Configuration changes should be logged with the same rigor as code changes.

### Flag Breaking Changes

If a change modifies the output schema of a job, changes the behavior of a shared joblet, or alters the execution order of a pipeline, the entry should prominently note this and list all downstream consumers that may be affected. A header like "BREAKING CHANGE" at the top of the summary paragraph makes these entries visually distinct during incident investigation.

### Review During Incident Response

When a production issue is reported, the first diagnostic step should be to review recent change log entries for the affected region and job family. This practice only works if the log is trustworthy and current. Teams should establish a habit of checking the change log before diving into logs, metrics, or code diffs.

### Archive Periodically

After a quarter or a major release, move older entries to an archive file (e.g., `12_CHANGE_LOG_ARCHIVE_2025_Q4.md`) to keep the active log focused on recent changes. Retain the archive indefinitely for audit purposes. The main change log file should contain only the current quarter's entries plus any unresolved entries from prior periods.

---

## Initial Entries

The following entries should be recorded as the baseline when the migration target platform is deployed. They capture the starting state rather than a specific change, establishing the foundation for all future change tracking.

```markdown
## [2026-XX-XX] -- Platform Migration: Initial Deployment

**Author:** [Migration team lead]
**Ticket:** [Migration project epic ticket]
**Environment:** PROD
**Deployment date:** [Date of production cutover]

### Summary

Initial deployment of the migrated Vaxxinova integration platform. All 257
Talend jobs have been migrated to [target platform]. This entry marks the
beginning of structured change tracking for the platform. Prior changes
are not recorded; see the Talend project's Git history for historical
reference.

### Affected Jobs

All jobs across all regions:
- BRA: 75 jobs
- USA: 61 jobs
- Global: 43 jobs
- JPN: 36 jobs
- GENERIC: 15 jobs
- DEU: 12 jobs
- ITA: 6 jobs
- scratchpad: 5 jobs
- ZAF: 3 jobs
- SAF: 1 job

### Rollback Plan

Full rollback to the Talend platform is available via [rollback procedure
document reference]. The Talend environment will be maintained in read-only
mode for [N] days after migration as a fallback. Rollback requires:
1. Redirecting orchestration triggers back to the Talend server
2. Verifying Talend context variables still point to current PROD resources
3. Running a full refresh of all regional pipelines to catch up on any
   data processed only by the new platform during the migration window
```

```markdown
## [2026-XX-XX] -- Platform Migration: Change Tracking Established

**Author:** [Migration team lead]
**Ticket:** [Migration project ticket]
**Environment:** Both

### Summary

Established the change tracking process for the migrated platform. All
future changes to pipeline logic, configuration, or schema must include
a change log entry following the template in this document. Pull requests
without a change log entry will not be approved.

### Testing

Validated that the change log template renders correctly in the project's
documentation system. Verified that the CI/CD pipeline includes a check
for change log entry presence in pull requests.

### Rollback Plan

Not applicable -- this is a process change, not a code change.
```

---

*Generated by intodoc -- documentation automation for integration platforms.*
