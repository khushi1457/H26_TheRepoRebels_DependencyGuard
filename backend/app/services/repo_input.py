"""Accept a local folder or a public GitHub URL and return a local path to scan."""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

_GITHUB_RE = re.compile(r"^https://github\.com/[\w.-]+/[\w.-]+?(\.git)?/?$")
_clones: dict[str, Path] = {}


def resolve_repo(source: str) -> Path:
    source = source.strip()
    if source.startswith("http"):
        if not _GITHUB_RE.match(source):
            raise ValueError("Only public https://github.com/<owner>/<repo> URLs are supported.")
        if source in _clones and _clones[source].exists():
            return _clones[source]
        dest = Path(tempfile.mkdtemp(prefix="depguard-clone-")) / "repo"
        proc = subprocess.run(["git", "clone", "--depth", "1", source, str(dest)],
                              capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            shutil.rmtree(dest.parent, ignore_errors=True)
            raise ValueError(f"git clone failed: {proc.stderr.strip()[:300]}")
        _clones[source] = dest
        return dest
    path = Path(source).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"Folder not found: {path}")
    return path
