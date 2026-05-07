from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class InstructionPack:
    path: Path
    version: str
    preambles: dict[str, str] = field(default_factory=dict)


class PackLoader:
    def __init__(self, pack_dir: Path) -> None:
        self.pack_dir = pack_dir

    def load(self) -> InstructionPack:
        version_file = self.pack_dir / "version.txt"
        if version_file.exists():
            version = version_file.read_text(encoding="utf-8").strip()
        else:
            version = self.pack_dir.name

        preambles: dict[str, str] = {}
        if self.pack_dir.exists():
            for md_file in sorted(self.pack_dir.glob("*.md")):
                phase = md_file.stem  # e.g. "phase2_preamble" → key "phase2_preamble"
                preambles[phase] = md_file.read_text(encoding="utf-8")

        return InstructionPack(path=self.pack_dir, version=version, preambles=preambles)

    def get(self, pack: InstructionPack, phase: str, fallback: str) -> str:
        return pack.preambles.get(phase, fallback)
