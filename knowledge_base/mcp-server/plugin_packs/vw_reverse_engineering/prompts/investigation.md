You are gathering reverse-engineering evidence from a Talend project export.
Downstream documents fill a client interface template and must be traceable to
source, so collect facts at implementation depth and always keep the source
reference (job file + component unique name).

For each job in this scanner group, record:

- job identity: name, version, folder/group, and one-sentence purpose
- entry point: is this job invoked by REST (tRESTRequest), by a parent job
  (tRunJob), by a route (cTalendJob), by a schedule, or standalone — quote the
  evidencing component and its key settings
- components in execution order, with the settings that carry meaning:
  SQL query text (verbatim, including string concatenation with context vars),
  file paths, HTTP/REST endpoints and methods, tMap input/output tables and
  expressions, tSetGlobalVar assignments, tJava/tJavaRow code
- external systems touched: databases, file shares, APIs, queues, mail
- context variables referenced (context.*) and, from the context groups,
  their per-environment values (Default/DEV/TST/ACC/PROD). Keep credential
  variable names but never reproduce secret values — note the type and that a
  value exists (redacted / enc: / cleartext)
- error handling present: OnSubjobError / OnComponentError links, tDie, tWarn,
  retry loops, log writes (which table/columns), notifications
- dependencies: parent/child jobs (tRunJob), shared routines, shared connections

Record every gap against the job it affects. Do not fill gaps with assumptions
at this stage — later documents decide how to present each gap. Use only the
supplied scanner evidence. Prefer structured bullets and small tables.
