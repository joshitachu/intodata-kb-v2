#!/usr/bin/env python3
"""Scan text for invisible characters that can hide instructions from a human reader.

Why this exists for this repo specifically: intodoc ingests client source trees and
generates documentation from them. A source file, commit message, or README in a
client codebase can carry characters that render as nothing in an editor or a diff,
but are read normally by a model. That is the "ASCII smuggling" injection vector --
the Unicode Tag block (U+E0000-U+E007F) can encode an entire instruction that a human
reviewing the file cannot see.

Bidirectional overrides are the second class: they let displayed text differ from
logical text, so a line can read as a comment while parsing as code.

This also enforces the repo's existing ASCII-only rule for generated Markdown from a
different angle -- that rule exists for Windows cp1252 compatibility, and this catches
the security half of the same problem.

Usage:
    scan-invisible-chars.py                  scan the default targets
    scan-invisible-chars.py PATH [PATH ...]  scan specific files or directories

Exit 0 = clean, 1 = findings. Intended for the validate skill and for scanning a
freshly ingested client tree before generation runs.
"""
import os
import sys
import unicodedata

# Scanned when no paths are given: the agent config, project instructions, and the
# generated-doc output tree.
DEFAULT_TARGETS = [
    ".claude", ".codex", "knowledge_base/.agent",
    "CLAUDE.md", "knowledge_base/CLAUDE.md", "RAG_pipeline/CLAUDE.md",
    "knowledge_base/output",
]

TEXT_EXT = (".md", ".txt", ".py", ".ts", ".tsx", ".js", ".jsx", ".json",
            ".yaml", ".yml", ".toml", ".sh", ".sql", ".xml", ".item", ".properties")

SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "site-packages",
             "__pycache__", ".next", "dist", "build", ".pytest_cache", ".ruff_cache"}


def classify(cp: int) -> str | None:
    """Return a reason string if this codepoint is invisible-and-dangerous."""
    if cp in (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF):
        return "zero-width / word-joiner / BOM"
    if 0x202A <= cp <= 0x202E:
        return "bidirectional override (display can differ from logical order)"
    if 0x2066 <= cp <= 0x2069:
        return "bidirectional isolate"
    if 0xE0000 <= cp <= 0xE007F:
        return "UNICODE TAG BLOCK - the ASCII-smuggling prompt-injection vector"
    if 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF:
        return "variation selector"
    if cp in (0x180E, 0x115F, 0x1160, 0x3164, 0xFFA0):
        return "invisible filler"
    if 0x2061 <= cp <= 0x2064:
        return "invisible math operator"
    return None


def scan_file(path: str) -> list[tuple[int, int, int, str]]:
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except (OSError, UnicodeDecodeError):
        return []
    out = []
    for lineno, line in enumerate(lines, 1):
        for col, ch in enumerate(line, 1):
            reason = classify(ord(ch))
            if reason:
                out.append((lineno, col, ord(ch), reason))
    return out


def walk(target: str):
    if os.path.isfile(target):
        yield target
        return
    for root, dirs, names in os.walk(target):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for n in names:
            if n.endswith(TEXT_EXT):
                yield os.path.join(root, n)


def main(argv: list[str]) -> int:
    targets = argv[1:] or DEFAULT_TARGETS
    findings, scanned = [], 0

    for t in targets:
        if not os.path.exists(t):
            continue
        for path in walk(t):
            scanned += 1
            for lineno, col, cp, reason in scan_file(path):
                findings.append((path, lineno, col, cp, reason))

    if not findings:
        print(f"CLEAN -- {scanned} files scanned, no invisible characters found.")
        return 0

    print(f"FOUND {len(findings)} invisible character(s) across {scanned} files:\n")
    for path, lineno, col, cp, reason in findings[:60]:
        try:
            name = unicodedata.name(chr(cp))
        except ValueError:
            name = "unnamed"
        print(f"  {path}:{lineno}:{col}")
        print(f"      U+{cp:04X} ({name}) -- {reason}")
    if len(findings) > 60:
        print(f"\n  ... and {len(findings) - 60} more")

    print("\nA human reading these files cannot see these characters; a model can.")
    print("If any appear in an ingested client tree, treat it as a possible injection")
    print("attempt and do not run generation over that source until reviewed.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
