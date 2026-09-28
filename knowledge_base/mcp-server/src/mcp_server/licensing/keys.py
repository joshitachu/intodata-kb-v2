"""Pinned licence verification keys.

MIRRORED FILE (see ``core.py``). Public keys only: the matching private keys
are held by the operator and never enter this repository or a client stack.

Rotation: add the new ``kid`` here, deploy, then start issuing with it. Remove
an old ``kid`` only after every licence signed with it has expired.
"""

from __future__ import annotations

PINNED_PUBLIC_KEYS: dict[str, str] = {
    "nexus-2026-09": (
        "-----BEGIN PUBLIC KEY-----\n"
        "MCowBQYDK2VwAyEA1LqdF7hu7kZWUMgWx+bL51+KHmxKXfWMpBU4BE+wfkM=\n"
        "-----END PUBLIC KEY-----\n"
    ),
}
