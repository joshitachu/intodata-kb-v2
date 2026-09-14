---
name: regex-vs-llm-parsing
description: Decide between deterministic parsing and an LLM for structured text, and combine them safely - regex first, confidence scoring, LLM only for the low-confidence tail. Use when writing or debugging a parser for logs, scanner output, or any semi-structured source.
---

# Regex vs LLM for Structured Text

Deterministic parsing handles the bulk of consistent formats cheaply and repeatably.
An LLM is for the tail. The mistake is picking one for the whole job.

## Choose

```
Is the format consistent and repeating?
  Yes (>90% follows a pattern) -> parse deterministically
      handles >95%  -> done, no LLM
      handles <95%  -> add an LLM pass for the flagged remainder only
  No  (free-form, highly variable) -> LLM directly
```

## The failure this repo has already had

A runtime-log parser was calibrated against seed fixtures. It matched the fixtures, so
it looked finished -- but real TMC output differed, and everything the patterns did not
recognise was silently dropped rather than flagged. Meaning was discarded without a
single error. Recalibrating against real logs moved wrong-platform diagnoses from 14 to
0 and specific diagnoses from 2 to 40.

Two rules come out of that, and they matter more than the regexes:

1. **Calibrate on real source data, never on fixtures you wrote.** Fixtures encode the
   patterns you already thought of.
2. **Unmatched input must be counted and surfaced, never dropped.** A parser that
   silently discards what it does not understand reports success while losing data.

## Confidence scoring is the handoff mechanism

Do not guess which items need an LLM. Score them.

```python
@dataclass(frozen=True)
class Confidence:
    item_id: str
    score: float
    reasons: tuple[str, ...]


def score(item: Parsed) -> Confidence:
    reasons, s = [], 1.0
    if not item.required_field:
        reasons.append("missing_required"); s -= 0.5
    if item.unmatched_chars > 0.2 * item.total_chars:
        reasons.append("mostly_unmatched"); s -= 0.4
    if item.matched_rule == "fallback":
        reasons.append("fallback_rule"); s -= 0.3
    return Confidence(item.id, max(0.0, s), tuple(reasons))


def needs_review(items, threshold: float = 0.95) -> list[Confidence]:
    return [c for c in map(score, items) if c.score < threshold]
```

Send only `needs_review` to the model. Everything else is already done.

## Pipeline shape

```python
parsed          = [parse(line) for line in source]     # deterministic, pure
low_confidence  = needs_review(parsed)
repaired        = llm_repair(low_confidence) if low_confidence else []
result          = merge(parsed, repaired)              # returns new objects

logger.info("parsed=%d llm=%d (%.1f%% deterministic)",
            len(parsed), len(low_confidence),
            100 * (1 - len(low_confidence) / max(1, len(parsed))))
```

That log line is the health metric. If the deterministic share starts falling, the
source format changed and the patterns need revisiting -- you want to find that in a
log, not in a customer's document.

## Rules

- Start deterministic even if the first version is imperfect; it gives you a baseline
  and a measurable success rate.
- Use the cheapest capable model for the repair pass. It is doing narrow work.
- Never mutate parsed items during repair -- return new ones, so the deterministic
  result stays inspectable.
- Test malformed input, missing fields, and encoding explicitly. Those are the tail
  you are building the LLM pass for.

## Anti-patterns

- Sending everything to a model when patterns handle nearly all of it.
- Pattern-matching genuinely free-form text.
- No confidence scoring -- hoping the parser "just works".
- Counting a run as successful without reporting how much input went unmatched.
