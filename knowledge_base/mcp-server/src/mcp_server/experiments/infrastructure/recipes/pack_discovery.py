from pathlib import Path
import stat


def discover_agentic_recipe(*, pack_root: Path, certified: bool) -> Path | None:
    if not certified:
        return None
    path = pack_root / "agentic.yaml"
    try:
        mode = path.lstat().st_mode
    except OSError:
        return None
    return path if stat.S_ISREG(mode) and not stat.S_ISLNK(mode) else None
