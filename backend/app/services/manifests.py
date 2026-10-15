"""Find and parse dependency declarations in a Python repository.

Supported:
  * requirements*.txt / *.in files and files inside a requirements/ folder,
    including -r / -c includes
  * pyproject.toml: PEP 621 [project], [build-system], PEP 735
    [dependency-groups], and Poetry's [tool.poetry] tables
  * setup.cfg: [options] install_requires and extras_require

Reported but not read:
  * setup.py and Pipfile. setup.py is executable Python, and DependencyGuard
    never runs repository code.

Nothing here is specific to any one repository.

Run directly to inspect a repository:
    python -m app.services.manifests D:\\LinkWatch
"""

import configparser
import re
import sys
import tomllib
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from app.schemas.scan import (
    DependencyDeclaration,
    ManifestFile,
    ManifestScanResult,
    Scope,
    UnparsedEntry,
)
from app.services.repo_files import is_inside, iter_repo_files, read_text, rel, resolve_root

_REQ_FILE_RE = re.compile(r"^requirements.*\.(txt|in)$", re.IGNORECASE)
_DEV_WORDS = {"dev", "develop", "test", "tests", "testing", "lint", "docs", "doc", "ci", "typing"}
_INLINE_COMMENT_RE = re.compile(r"(^|\s)#.*$")
_TRAILING_OPTIONS_RE = re.compile(r"\s--?[a-zA-Z].*$")
_POETRY_EXACT_RE = re.compile(r"^\d+(\.\d+)*$")


# --------------------------------------------------------------------------- helpers

def _is_requirements_file(path: Path) -> bool:
    return bool(_REQ_FILE_RE.match(path.name)) or (
        path.parent.name.lower() == "requirements" and path.suffix.lower() in {".txt", ".in"}
    )


def _scope_from_filename(path: Path) -> Scope:
    """Guess scope from the file name: requirements-dev.txt -> dev, else runtime.

    This is a naming heuristic, not a guarantee. It is labelled as such in the
    scope value so later phases treat it with appropriate weight.
    """
    words = set(re.split(r"[-_.]", path.stem.lower()))
    return "dev" if words & _DEV_WORDS else "runtime"


def _find_line(lines: list[str], needle: str) -> int | None:
    for number, text in enumerate(lines, start=1):
        if needle in text:
            return number
    return None


def _declaration(req: Requirement, *, raw: str, scope: Scope, source: str,
                 line: int | None, group: str | None = None) -> DependencyDeclaration:
    specs = list(req.specifier)
    pinned = None
    if len(specs) == 1 and specs[0].operator in ("==", "===") and "*" not in specs[0].version:
        pinned = specs[0].version
    return DependencyDeclaration(
        name=req.name,
        normalized_name=canonicalize_name(req.name),
        specifier=str(req.specifier),
        pinned_version=pinned,
        extras=sorted(req.extras),
        marker=str(req.marker) if req.marker else None,
        scope=scope,
        group=group,
        source_file=source,
        line=line,
        raw=raw,
    )


class _Collector:
    def __init__(self, root: Path):
        self.root = root
        self.manifests: list[ManifestFile] = []
        self.declarations: list[DependencyDeclaration] = []
        self.unparsed: list[UnparsedEntry] = []

    def add_pep508(self, raw: str, *, scope: Scope, source: str, line: int | None,
                   group: str | None = None) -> None:
        try:
            req = Requirement(raw)
        except InvalidRequirement as exc:
            self.unparsed.append(UnparsedEntry(source_file=source, line=line, raw=raw,
                                               reason=f"Not a valid PEP 508 requirement: {exc}"))
            return
        self.declarations.append(
            _declaration(req, raw=raw, scope=scope, source=source, line=line, group=group))


# --------------------------------------------------------------------------- requirements.txt

def _logical_lines(text: str):
    """Yield (first_line_number, joined_text), honouring trailing-backslash continuations."""
    buffer: list[str] = []
    start = None
    for number, line in enumerate(text.splitlines(), start=1):
        if start is None:
            start = number
        if line.rstrip().endswith("\\"):
            buffer.append(line.rstrip()[:-1])
            continue
        buffer.append(line)
        yield start, " ".join(buffer)
        buffer, start = [], None
    if buffer:
        yield start, " ".join(buffer)


def _parse_requirements(path: Path, c: _Collector, queue: list[tuple[Path, bool]],
                        is_constraints: bool) -> None:
    source = rel(path, c.root)
    text = read_text(path)
    if text is None:
        c.manifests.append(ManifestFile(path=source, kind="requirements", parsed=False,
                                        note="Unreadable, not UTF-8, or too large"))
        return
    c.manifests.append(ManifestFile(path=source, kind="requirements", parsed=True,
                                    note="constraints file" if is_constraints else None))
    scope: Scope = "constraint" if is_constraints else _scope_from_filename(path)

    for line_no, original in _logical_lines(text):
        line = _INLINE_COMMENT_RE.sub("", original).strip()
        if not line:
            continue

        include = re.match(r"^(-r|--requirement|-c|--constraint)\s*=?\s*(\S+)", line)
        if include:
            flag, target = include.groups()
            target_path = (path.parent / target)
            if target_path.is_file() and is_inside(target_path, c.root):
                queue.append((target_path.resolve(), flag in ("-c", "--constraint")))
            else:
                c.unparsed.append(UnparsedEntry(source_file=source, line=line_no, raw=original.strip(),
                                                reason="Included file not found inside the repository"))
            continue

        if line.startswith(("-e", "--editable")):
            c.unparsed.append(UnparsedEntry(source_file=source, line=line_no, raw=original.strip(),
                                            reason="Editable install (local path or VCS); no version to analyse"))
            continue
        if line.startswith("-"):
            continue  # pip options such as --index-url; not dependencies

        line = _TRAILING_OPTIONS_RE.sub("", line)  # e.g. trailing --hash=sha256:...
        c.add_pep508(line, scope=scope, source=source, line=line_no)


# --------------------------------------------------------------------------- pyproject.toml

def _parse_pyproject(path: Path, c: _Collector) -> None:
    source = rel(path, c.root)
    text = read_text(path)
    try:
        data = tomllib.loads(text) if text is not None else None
    except tomllib.TOMLDecodeError as exc:
        c.manifests.append(ManifestFile(path=source, kind="pyproject", parsed=False,
                                        note=f"Invalid TOML: {exc}"))
        return
    if data is None:
        c.manifests.append(ManifestFile(path=source, kind="pyproject", parsed=False,
                                        note="Unreadable, not UTF-8, or too large"))
        return
    c.manifests.append(ManifestFile(path=source, kind="pyproject", parsed=True))
    lines = text.splitlines()

    def add_list(items, scope: Scope, group: str | None = None):
        for raw in items or []:
            if isinstance(raw, str):  # PEP 735 groups may contain {include-group = ...} tables
                c.add_pep508(raw, scope=scope, source=source,
                             line=_find_line(lines, f'"{raw}"') or _find_line(lines, f"'{raw}'"),
                             group=group)

    project = data.get("project", {})
    add_list(project.get("dependencies"), "runtime")
    for group, items in (project.get("optional-dependencies") or {}).items():
        add_list(items, "optional", group)
    add_list(data.get("build-system", {}).get("requires"), "build")
    for group, items in (data.get("dependency-groups") or {}).items():
        add_list(items, "group", group)

    poetry = data.get("tool", {}).get("poetry", {})
    if poetry:
        _parse_poetry(poetry, lines, source, c)


def _parse_poetry(poetry: dict, lines: list[str], source: str, c: _Collector) -> None:
    tables: list[tuple[dict, Scope, str | None]] = [(poetry.get("dependencies") or {}, "runtime", None),
                                                     (poetry.get("dev-dependencies") or {}, "dev", None)]
    for group, body in (poetry.get("group") or {}).items():
        tables.append((body.get("dependencies") or {}, "group", group))

    for table, scope, group in tables:
        for name, value in table.items():
            if name.lower() == "python":
                continue
            version = value.get("version", "") if isinstance(value, dict) else str(value)
            line = next((n for n, t in enumerate(lines, 1) if re.match(rf"^\s*{re.escape(name)}\s*=", t)), None)
            if isinstance(value, dict) and not version:
                c.unparsed.append(UnparsedEntry(
                    source_file=source, line=line, raw=f"{name} = {value}",
                    reason="Poetry dependency from git/path/url; no version to analyse"))
                continue
            # Poetry's ^ and ~ syntax is not PEP 440, so it is kept verbatim.
            c.declarations.append(DependencyDeclaration(
                name=name, normalized_name=canonicalize_name(name),
                specifier="" if version == "*" else version,
                pinned_version=version if _POETRY_EXACT_RE.match(version) else None,
                scope=scope, group=group, source_file=source, line=line,
                raw=f"{name} = {value!r}" if isinstance(value, dict) else f'{name} = "{value}"',
            ))


# --------------------------------------------------------------------------- setup.cfg

def _parse_setup_cfg(path: Path, c: _Collector) -> None:
    source = rel(path, c.root)
    text = read_text(path)
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(text or "")
    except configparser.Error as exc:
        c.manifests.append(ManifestFile(path=source, kind="setup.cfg", parsed=False, note=str(exc)))
        return
    if not parser.has_section("options") and not parser.has_section("options.extras_require"):
        return  # setup.cfg that only configures tools (flake8, etc.); not a manifest
    c.manifests.append(ManifestFile(path=source, kind="setup.cfg", parsed=True))
    lines = (text or "").splitlines()

    def add_block(block: str, scope: Scope, group: str | None = None):
        for raw in block.splitlines():
            raw = _INLINE_COMMENT_RE.sub("", raw).strip()
            if raw:
                c.add_pep508(raw, scope=scope, source=source, line=_find_line(lines, raw), group=group)

    if parser.has_option("options", "install_requires"):
        add_block(parser.get("options", "install_requires"), "runtime")
    if parser.has_section("options.extras_require"):
        for group, block in parser.items("options.extras_require"):
            add_block(block, "optional", group)


# --------------------------------------------------------------------------- entry point

def scan_manifests(root_path: str) -> ManifestScanResult:
    root = resolve_root(root_path)
    c = _Collector(root)
    queue: list[tuple[Path, bool]] = []

    for path in iter_repo_files(root):
        name = path.name.lower()
        if _is_requirements_file(path):
            queue.append((path.resolve(), False))
        elif name == "pyproject.toml":
            _parse_pyproject(path, c)
        elif name == "setup.cfg":
            _parse_setup_cfg(path, c)
        elif name == "setup.py":
            c.manifests.append(ManifestFile(
                path=rel(path, root), kind="setup.py", parsed=False,
                note="setup.py is executable code; DependencyGuard does not run repository code"))
        elif name == "pipfile":
            c.manifests.append(ManifestFile(path=rel(path, root), kind="pipfile", parsed=False,
                                            note="Pipfile parsing not implemented yet"))

    seen: set[Path] = set()
    while queue:  # -r / -c includes are appended while parsing; `seen` prevents loops
        path, is_constraints = queue.pop(0)
        if path in seen:
            continue
        seen.add(path)
        _parse_requirements(path, c, queue, is_constraints)

    c.manifests.sort(key=lambda m: m.path)
    return ManifestScanResult(manifests=c.manifests, declarations=c.declarations, unparsed=c.unparsed)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m app.services.manifests <path-to-repository>")
    try:
        result = scan_manifests(sys.argv[1])
    except ValueError as exc:
        sys.exit(str(exc))
    print(result.model_dump_json(indent=2))
