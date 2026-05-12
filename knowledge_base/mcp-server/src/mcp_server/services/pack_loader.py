from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class InstructionPack:
    path: Path
    version: str
    preambles: dict[str, str] = field(default_factory=dict)
    questions: list[dict] = field(default_factory=list)


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

        questions: list[dict] = []
        questionnaire_file = self.pack_dir / "questionnaire.json"
        if questionnaire_file.exists():
            try:
                data = json.loads(questionnaire_file.read_text(encoding="utf-8"))
                questions = data.get("questions", [])
            except (OSError, json.JSONDecodeError):
                pass

        return InstructionPack(path=self.pack_dir, version=version, preambles=preambles, questions=questions)

    def get(self, pack: InstructionPack, phase: str, fallback: str) -> str:
        return pack.preambles.get(phase, fallback)
