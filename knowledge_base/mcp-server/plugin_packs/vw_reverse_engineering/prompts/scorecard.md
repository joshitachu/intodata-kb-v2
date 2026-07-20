You are scoring a set of reverse-engineered Talend interfaces for a
rationalisation decision. Score from the supplied analysis evidence only; cite
the interface and component behind each score. Do not invent runtime data.

Produce:

- A short scorecard across these dimensions, each rated LOW / MEDIUM / HIGH with
  a one-line evidenced justification:
  - Security (credential handling, auth strength, injection exposure, secret storage)
  - Reliability (error handling, retries, dead-letter, silent-failure behaviour)
  - Maintainability (dead code, copy-paste artifacts, hardcoded paths, naming)
  - Observability (logging, monitoring, alerting present in the jobs)
  - Migration readiness (how self-contained and re-platformable each interface is)
- A Top Risks register: for each risk give severity (HIGH/MEDIUM/LOW), type
  (security / reliability / config / tech-debt), the interface(s) affected, and
  the source reference. Prefer concrete, reproducible findings (e.g. "PROD URL
  points at a test host", "caller receives HTTP 200 when target is unreachable")
  over generic statements.
- Confidence and limitations: state that this is static analysis with no runtime
  observation, and list the dimensions that cannot be scored from the export
  (throughput, historical error rate, real schedules) as validation items.

Keep every rating traceable. Where evidence is absent, say so rather than
guessing a score.
