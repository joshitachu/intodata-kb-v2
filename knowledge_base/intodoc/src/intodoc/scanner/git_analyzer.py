"""Git history analysis using GitPython."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from rich.console import Console

console = Console()


def analyze_git_history(repo_root: Path, max_commits: int = 500) -> dict:
    """Analyze git history for the repository.

    Returns dict with: recent_commits, hot_files, contributors, change_frequency.
    """
    try:
        import git
    except ImportError:
        console.print("[yellow]GitPython not available. Skipping git analysis.[/yellow]")
        return {}

    try:
        repo = git.Repo(repo_root, search_parent_directories=True)
    except git.InvalidGitRepositoryError:
        console.print("[yellow]Not a git repository. Skipping git analysis.[/yellow]")
        return {}

    commits = list(repo.iter_commits(max_count=max_commits))

    if not commits:
        return {"recent_commits": [], "hot_files": [], "contributors": [], "change_frequency": {}}

    # Recent commits
    recent = []
    for c in commits[:20]:
        recent.append({
            "hash": c.hexsha[:8],
            "message": c.message.strip().split("\n")[0][:100],
            "author": str(c.author),
            "date": c.committed_datetime.isoformat(),
        })

    # Hot files (most frequently changed)
    file_changes: Counter = Counter()
    contributors: Counter = Counter()

    for c in commits:
        contributors[str(c.author)] += 1
        for f in c.stats.files:
            file_changes[f] += 1

    hot_files = [
        {"file": f, "changes": n}
        for f, n in file_changes.most_common(30)
    ]

    contributor_list = [
        {"name": name, "commits": count}
        for name, count in contributors.most_common(20)
    ]

    return {
        "recent_commits": recent,
        "hot_files": hot_files,
        "contributors": contributor_list,
        "total_commits": len(commits),
        "latest_hash": commits[0].hexsha if commits else "",
    }
