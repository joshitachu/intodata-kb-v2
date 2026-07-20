"""Slice 3 read-only primary runtime evaluation package (sanitized reports only)."""

from .runner import run_authenticated, run_deterministic

__all__ = ["run_authenticated", "run_deterministic"]
