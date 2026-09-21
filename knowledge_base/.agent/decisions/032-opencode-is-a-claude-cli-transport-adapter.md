# ADR-032: OpenCode is a Claude CLI transport adapter

## Decision

OpenCode documentation generation reuses the canonical Claude CLI prompt and
the normal coordinator preprocessing, staging, validation and publication lane.
Only executable invocation, runtime credential/environment isolation, JSON
event parsing and usage accounting are OpenCode-specific.

## Consequences

The former Gather/Analyze/Produce scheduler, goal-driven contracts,
agent-workspace provider and OpenCode patch executor are retired. New and
resumed requests carrying those profiles fail explicitly; historical profile
metadata remains readable but has no executable meaning.
