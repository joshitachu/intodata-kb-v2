"""Shared access to the generation method for the prompt tests.

The four-phase procedure used to be an f-string inside
``providers/claude_cli.py::_build_prompt``, so every rule about HOW a run works
was asserted against the rendered prompt.  It is now
``data/generation_method/METHOD.md``, staged to ``<staging>/_method/METHOD.md``
and read by the agent.  The assertions did not go away with the f-string --
they moved here, because the rule they pin is the same rule.

``squash`` exists so that an assertion pins MEANING and not line wrapping.  The
old tests embedded the prompt's exact newlines (``"EVERY job in the group
must\\n      still be named"``), which made every re-wrap a test failure and
every test failure ambiguous: you could not tell a lost rule from a moved line
break.
"""

from __future__ import annotations

import re
from functools import cache

from mcp_server.services.generation_method import method_source_path


@cache
def method_text() -> str:
    """The method exactly as it is staged for a run."""
    return method_source_path().read_text(encoding="utf-8")


def squash(text: str) -> str:
    """Collapse every run of whitespace to one space.

    Applied to both haystack and needle, so an assertion survives re-wrapping
    but still fails when a rule is deleted or reworded.
    """
    return re.sub(r"\s+", " ", text).strip()


@cache
def method() -> str:
    """The method, squashed, for ``in`` assertions."""
    return squash(method_text())


def states(*phrases: str) -> list[str]:
    """Phrases the method does NOT state, squashed-compared. Empty == good."""
    body = method()
    return [phrase for phrase in phrases if squash(phrase) not in body]
