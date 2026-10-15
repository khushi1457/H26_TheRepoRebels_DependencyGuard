"""Safe, read-only traversal of a repository on disk.

DependencyGuard will scan repositories it does not trust, so this module
only ever *reads* files: it never imports or executes repository code,
never follows symlinks (which could point outside the repository), and
skips virtual environments, caches and VCS metadata.
"""

import os
from collections.abc import Iterator
from pathlib import Path

SKIP_DIR_NAMES = frozenset({
    ".git", ".hg", ".svn",
    ".venv", "venv",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".tox", ".nox", ".eggs",
    "node_modules", "site-packages",
    "build", "dist",
    ".idea", ".vscode",
})

# Files larger than this are almost never hand-written manifests or source.
DEFAULT_MAX_BYTES = 2 * 1024 * 1024


def _should_skip_dir(path: Path) -> bool:
    name = path.name
    if name in SKIP_DIR_NAMES or name.endswith(".egg-info"):
        return True
    if path.is_symlink():
        return True
    # Any folder containing pyvenv.cfg is a virtual environment, whatever its name.
    # A folder containing .dependencyguard-self is DependencyGuard itself (when it lives
    # inside a fork of the repository it scans) and must not be scanned as the target.
    return (path / "pyvenv.cfg").is_file() or (path / ".dependencyguard-self").is_file()


def resolve_root(root: str | os.PathLike) -> Path:
    path = Path(root).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"Repository path does not exist or is not a folder: {path}")
    return path


def iter_repo_files(root: Path) -> Iterator[Path]:
    """Yield every regular, non-symlink file under root, in a stable order."""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        # Editing dirnames in place tells os.walk not to descend into skipped folders.
        dirnames[:] = sorted(d for d in dirnames if not _should_skip_dir(current / d))
        for name in sorted(filenames):
            path = current / name
            if path.is_file() and not path.is_symlink():
                yield path


def is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
        return True
    except ValueError:
        return False


def rel(path: Path, root: Path) -> str:
    """Repository-relative path with forward slashes (same on Windows and Linux)."""
    return path.relative_to(root).as_posix()


def read_text(path: Path, max_bytes: int = DEFAULT_MAX_BYTES) -> str | None:
    """Read a text file, or return None if it is too large or not valid text."""
    try:
        if path.stat().st_size > max_bytes:
            return None
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None
