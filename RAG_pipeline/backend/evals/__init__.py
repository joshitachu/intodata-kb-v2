"""Answer-quality evaluation harness for the RAG / agent answer path.

This package is deliberately decoupled from ``src/``. It scores *answers*, not
implementations, so it can run:

* offline, against recorded answers (``fixtures`` mode) -- this is what CI runs;
* live, against a running backend over HTTP (``live`` mode).

Nothing here imports ``src.*``. That is a hard rule: the retrieval and harness
packages are under active rewrite, and an eval suite that breaks when the
implementation is refactored is an eval suite nobody runs.

See ``README.md`` in this directory for the metric definitions and the
deterministic-vs-judge split.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
