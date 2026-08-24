"""Prompt-cache plumbing shared by every provider adapter.

Two jobs, both provider-agnostic:

1. **Wire stability.** Every upstream we use caches on an exact *byte* prefix of
   the serialized request. The tool-schema block is serialized FIRST by both the
   OpenAI-compatible and the Anthropic prompt templates, so one reordered dict
   key inside a JSON Schema silently invalidates the cache for the ENTIRE
   request — schemas, system prompt and all. `canonical_schema` removes that
   class of failure by sorting object keys at the wire boundary, which is where
   serialization is owned. It never reorders lists, because list order can carry
   meaning (`required`, `enum`, `prefixItems`).

2. **Measurement.** Cache behaviour is invisible unless the hit counts are read
   back off the response and recorded. `read_openai_cache_tokens` /
   `apply_cache_tokens` normalize the two different upstream shapes onto the
   single `UsageStats` invariant, and `log_cache_effect` puts the hit ratio in
   the logs so a regression is noticeable without a database query.

Measured against api.moonshot.ai (kimi-k2.5, 2026-08-20): the prefix cache is
automatic, granular to 128-token blocks, and reported as
`usage.prompt_tokens_details.cached_tokens`. There is no explicit cache marker
to set — see `docs` note in `openai_compat`.
"""
from __future__ import annotations

import hashlib
import json
import logging
from contextvars import ContextVar
from typing import TypeAlias

from src.generation.usage import StreamUsageStats, UsageStats

logger = logging.getLogger(__name__)

# Any value that survives a JSON round trip. Recursive, so it describes a whole
# JSON Schema rather than degrading to `Any` at the first level of nesting.
JsonValue: TypeAlias = (
    "None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]"
)

# Cache-fingerprint of the last tool block sent, scoped to the current
# request/task. A change mid-turn means every subsequent model call in that turn
# re-pays for the whole prefix, which is otherwise completely silent.
_last_tool_fingerprint: ContextVar[tuple[str, str] | None] = ContextVar(
    "prompt_cache_last_tool_fingerprint", default=None
)

# Below this share of the prompt we log the miss at INFO rather than staying
# quiet, so a broken prefix is visible in normal logs.
_LOW_HIT_RATIO = 0.25


def reset_prefix_tracking() -> None:
    """Forget the last tool fingerprint.

    Each HTTP request runs in its own task context, so the ContextVar is already
    turn-scoped in production. Tests share one context, so they call this between
    cases to avoid a stale fingerprint from a previous case.
    """
    _last_tool_fingerprint.set(None)


def canonical_schema(value: JsonValue) -> JsonValue:
    """Return `value` with every mapping's keys in sorted order, recursively.

    Byte-identical output for logically identical schemas, which is what a
    prefix cache actually keys on. Lists keep their order — only dict keys are
    normalized, since JSON object key order is semantically irrelevant while
    list order is not.
    """
    if isinstance(value, dict):
        return {key: canonical_schema(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [canonical_schema(item) for item in value]
    return value


def _fingerprint(payload: JsonValue) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def check_tool_prefix_stability(provider_id: str, tools_schema: list[dict] | None) -> None:
    """Warn when the tool block changes between calls in the same turn.

    The tool block heads the cacheable prefix on both wire formats, so a change
    here costs the full prefix on every following call. Emitting a warning turns
    an invisible cost regression into a searchable log line; it never alters the
    request.
    """
    if not tools_schema:
        return
    current = _fingerprint(tools_schema)
    previous = _last_tool_fingerprint.get()
    _last_tool_fingerprint.set((provider_id, current))
    if previous is None or previous[0] != provider_id:
        return
    if previous[1] != current:
        logger.warning(
            "%s tool schema block changed within this turn (%s -> %s, %d tools); "
            "the prompt prefix cache is invalidated for the rest of the turn",
            provider_id,
            previous[1],
            current,
            len(tools_schema),
        )


def read_openai_cache_tokens(usage: object | None) -> tuple[int, int]:
    """Extract (cached_input_tokens, cache_write_tokens) from an OpenAI-shaped usage.

    Reads `prompt_tokens_details.cached_tokens` first (the standard field, which
    Moonshot and OpenAI both populate), then falls back to the top-level
    `cached_tokens` that Moonshot additionally returns outside the typed model.
    `prompt_tokens_details` is None on a cold call, so every access is defensive.
    """
    if usage is None:
        return 0, 0
    details = getattr(usage, "prompt_tokens_details", None)
    cached = 0
    written = 0
    if details is not None:
        cached = _coerce_count(getattr(details, "cached_tokens", None))
        written = _coerce_count(getattr(details, "cache_write_tokens", None))
        if not written:
            written = _coerce_count(_from_extra(details, "cache_write_tokens"))
    if not cached:
        # Moonshot mirrors the count at the top level of `usage`; the OpenAI SDK
        # parks unknown fields in model_extra rather than dropping them.
        cached = _coerce_count(
            getattr(usage, "cached_tokens", None) or _from_extra(usage, "cached_tokens")
        )
    return cached, written


def _from_extra(obj: object, key: str) -> object | None:
    extra = getattr(obj, "model_extra", None)
    if isinstance(extra, dict):
        return extra.get(key)
    return None


def _coerce_count(value: object | None) -> int:
    """Token counts arrive as int, None, or (from model_extra) str. Never raise."""
    if value is None:
        return 0
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    if isinstance(value, str):
        try:
            return max(0, int(value.strip()))
        except ValueError:
            return 0
    return 0


def apply_cache_tokens(
    stats: UsageStats | StreamUsageStats, cached: int, written: int
) -> None:
    """Record cache counts on `stats`, clamped to the recorded prompt size.

    Clamping matters: an upstream that reports a cached count larger than
    `input_tokens` would otherwise drive `uncached_input_tokens` negative and
    understate cost. Cost must never be understated by a reporting quirk.
    """
    total = max(0, stats.input_tokens)
    stats.cached_input_tokens = min(max(0, cached), total)
    stats.cache_write_tokens = min(max(0, written), total - stats.cached_input_tokens)


def log_cache_effect(provider_id: str, model: str, stats: UsageStats) -> None:
    """Log the realized prefix-cache effect for one call."""
    if stats.input_tokens <= 0:
        return
    ratio = stats.cache_hit_ratio
    message = (
        "%s/%s prompt cache: %d/%d prompt tokens cached (%.0f%%), "
        "%d billed at full rate, %d cache-write"
    )
    args = (
        provider_id,
        model,
        stats.cached_input_tokens,
        stats.input_tokens,
        ratio * 100,
        stats.uncached_input_tokens,
        stats.cache_write_tokens,
    )
    if ratio < _LOW_HIT_RATIO:
        logger.info(message, *args)
    else:
        logger.debug(message, *args)
