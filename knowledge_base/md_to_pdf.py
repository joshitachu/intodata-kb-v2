"""Convert markdown files in knowledge_base/output/ to PDF using pandoc.

Usage:
    python md_to_pdf.py                  # convert all projects
    python md_to_pdf.py esb-main         # convert one project
    python md_to_pdf.py --list           # list available projects
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent / "output"


def check_pandoc() -> str:
    path = shutil.which("pandoc")
    if not path:
        print("ERROR: pandoc not found on PATH.")
        print("Install it from https://pandoc.org/installing.html")
        sys.exit(1)
    return path


def get_projects() -> list[Path]:
    if not OUTPUT_DIR.is_dir():
        print(f"ERROR: output directory not found: {OUTPUT_DIR}")
        sys.exit(1)
    return sorted(p for p in OUTPUT_DIR.iterdir() if p.is_dir())


def convert_project(project_dir: Path, pandoc: str) -> int:
    docs_dir = project_dir / "docs"
    if not docs_dir.is_dir():
        print(f"  Skipping {project_dir.name} (no docs/ folder)")
        return 0

    md_files = sorted(docs_dir.glob("*.md"))
    if not md_files:
        print(f"  Skipping {project_dir.name} (no .md files)")
        return 0

    pdf_dir = project_dir / "pdf"
    pdf_dir.mkdir(exist_ok=True)

    converted = 0
    for md_file in md_files:
        pdf_file = pdf_dir / md_file.with_suffix(".pdf").name
        cmd = [
            pandoc, str(md_file),
            "-o", str(pdf_file),
            "--pdf-engine=xelatex",
            "-V", "geometry:margin=1in",
            "-V", "fontsize=11pt",
            "--highlight-style=tango",
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            print(f"  {md_file.name} -> pdf/{pdf_file.name}")
            converted += 1
        except subprocess.CalledProcessError as e:
            print(f"  FAILED: {md_file.name}")
            if e.stderr:
                for line in e.stderr.strip().splitlines()[:5]:
                    print(f"    {line}")
    return converted


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert output markdown to PDF via pandoc")
    parser.add_argument("project", nargs="?", help="Project folder name (default: all)")
    parser.add_argument("--list", action="store_true", help="List available projects")
    args = parser.parse_args()

    projects = get_projects()

    if args.list:
        for p in projects:
            md_count = len(list((p / "docs").glob("*.md"))) if (p / "docs").is_dir() else 0
            print(f"  {p.name} ({md_count} md files)")
        return

    pandoc = check_pandoc()

    if args.project:
        target = OUTPUT_DIR / args.project
        if not target.is_dir():
            print(f"ERROR: project not found: {args.project}")
            print(f"Available: {', '.join(p.name for p in projects)}")
            sys.exit(1)
        projects = [target]

    total = 0
    for project in projects:
        print(f"[{project.name}]")
        total += convert_project(project, pandoc)

    print(f"\nDone. {total} file(s) converted.")


if __name__ == "__main__":
    main()
