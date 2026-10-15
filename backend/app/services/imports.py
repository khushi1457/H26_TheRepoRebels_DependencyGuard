"""Find every import in a Python repository using the AST.

The source is parsed with Python's `ast` module, never imported or run, so
this is safe on untrusted repositories.

Each import is classified as:
  * stdlib       - part of the Python standard library (sys.stdlib_module_names)
  * first_party  - the repository's own packages/modules
  * third_party  - everything else; these are what DependencyGuard analyses

Run directly to inspect a repository:
    python -m app.services.imports ../../LinkWatch
    python -m app.services.imports ../../LinkWatch --summary
"""

import ast
import sys
from collections import defaultdict
from pathlib import Path

from app.schemas.imports import (
    ImportedName,
    ImportOccurrence,
    ImportScanResult,
    ImportSummary,
    ParseFailure,
)
from app.services.repo_files import DEFAULT_MAX_BYTES, iter_repo_files, rel, resolve_root

STDLIB = frozenset(sys.stdlib_module_names) | {"__future__"}


def _is_private(name: str) -> bool:
    return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))


def find_first_party(root: Path, py_files: list[Path]) -> set[str]:
    """Names the repository itself provides.

    A package root is a folder with __init__.py whose parent has none.
    A top-level module is a .py file whose folder is not a package.
    (A local file named e.g. requests.py would shadow the real package
    when run from that folder, so classifying it first-party matches
    Python's own behaviour.)
    """
    names: set[str] = set()
    for path in py_files:
        parent = path.parent
        if path.name == "__init__.py":
            if not (parent.parent / "__init__.py").is_file() and parent != root:
                names.add(parent.name)
        elif not (parent / "__init__.py").is_file():
            names.add(path.stem)
    return names


class _ImportVisitor(ast.NodeVisitor):
    def __init__(self, source_file: str, first_party: set[str]):
        self.source_file = source_file
        self.first_party = first_party
        self.found: list[ImportOccurrence] = []
        self._function_depth = 0
        self._try_depth = 0
        self._type_checking_depth = 0

    # --- context tracking -------------------------------------------------
    def _visit_function(self, node):
        self._function_depth += 1
        self.generic_visit(node)
        self._function_depth -= 1

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function
    visit_Lambda = _visit_function

    def _visit_try(self, node):
        # Only the guarded body counts as "inside try"; handlers/else/finally do not.
        self._try_depth += 1
        for stmt in node.body:
            self.visit(stmt)
        self._try_depth -= 1
        for part in (node.handlers, node.orelse, node.finalbody):
            for stmt in part:
                self.visit(stmt)

    visit_Try = _visit_try
    visit_TryStar = _visit_try

    def visit_If(self, node):
        test = node.test
        is_tc = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")
        self.visit(test)
        if is_tc:
            self._type_checking_depth += 1
        for stmt in node.body:
            self.visit(stmt)
        if is_tc:
            self._type_checking_depth -= 1
        for stmt in node.orelse:
            self.visit(stmt)

    # --- imports ----------------------------------------------------------
    def _origin(self, top: str):
        if top in self.first_party:
            return "first_party"
        if top in STDLIB:
            return "stdlib"
        return "third_party"

    def _record(self, node, *, module: str, statement: str, names: list[ImportedName],
                module_alias: str | None = None):
        top = module.split(".")[0]
        self.found.append(ImportOccurrence(
            module=module, top_level=top, origin=self._origin(top), statement=statement,
            names=names, module_alias=module_alias, file=self.source_file,
            line=node.lineno, column=node.col_offset,
            inside_function=self._function_depth > 0,
            inside_try=self._try_depth > 0,
            type_checking_only=self._type_checking_depth > 0,
            private_names=sorted({n.name for n in names if _is_private(n.name)}
                                 | {p for p in module.split(".")[1:] if _is_private(p)}),
        ))

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            self._record(node, module=alias.name, statement="import", names=[],
                         module_alias=alias.asname)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.level and node.level > 0:
            return  # relative import (from . import x): always the repo's own code
        if not node.module:
            return
        names = [ImportedName(name=a.name, alias=a.asname) for a in node.names]
        self._record(node, module=node.module, statement="from", names=names)


def scan_imports(root_path: str) -> ImportScanResult:
    root = resolve_root(root_path)
    py_files = [p for p in iter_repo_files(root) if p.suffix == ".py"]
    first_party = find_first_party(root, py_files)

    occurrences: list[ImportOccurrence] = []
    failures: list[ParseFailure] = []

    for path in py_files:
        source_file = rel(path, root)
        try:
            if path.stat().st_size > DEFAULT_MAX_BYTES:
                failures.append(ParseFailure(file=source_file, reason="File too large to scan"))
                continue
            # Passing bytes lets ast honour '# -*- coding: ... -*-' declarations.
            tree = ast.parse(path.read_bytes(), filename=source_file)
        except SyntaxError as exc:
            failures.append(ParseFailure(file=source_file, line=exc.lineno,
                                         reason=f"SyntaxError: {exc.msg}"))
            continue
        except (ValueError, OSError) as exc:
            failures.append(ParseFailure(file=source_file, reason=f"{type(exc).__name__}: {exc}"))
            continue
        visitor = _ImportVisitor(source_file, first_party)
        visitor.visit(tree)
        occurrences.extend(visitor.found)

    by_top: dict[str, list[ImportOccurrence]] = defaultdict(list)
    for occ in occurrences:
        if occ.origin == "third_party":
            by_top[occ.top_level].append(occ)

    third_party = [
        ImportSummary(top_level=top, occurrences=len(occs), files=sorted({o.file for o in occs}))
        for top, occs in sorted(by_top.items())
    ]
    return ImportScanResult(
        python_files_scanned=len(py_files),
        first_party_modules=sorted(first_party),
        third_party=third_party,
        occurrences=occurrences,
        parse_failures=failures,
    )


def _print_summary(result: ImportScanResult) -> None:
    print(f"Python files scanned: {result.python_files_scanned}")
    print(f"First-party modules:  {', '.join(result.first_party_modules) or '-'}")
    print("\nThird-party imports:")
    for occ in result.occurrences:
        if occ.origin != "third_party":
            continue
        what = occ.module if occ.statement == "import" else \
            f"from {occ.module} import " + ", ".join(
                n.name + (f" as {n.alias}" if n.alias else "") for n in occ.names)
        if occ.module_alias:
            what += f" as {occ.module_alias}"
        flags = [f for f, on in (("lazy", occ.inside_function), ("try", occ.inside_try),
                                 ("type-only", occ.type_checking_only),
                                 ("PRIVATE API", bool(occ.private_names))) if on]
        print(f"  {occ.file}:{occ.line:<4} {what}" + (f"   [{', '.join(flags)}]" if flags else ""))
    print("\nParse failures:", len(result.parse_failures))
    for f in result.parse_failures:
        print(f"  {f.file}:{f.line}  {f.reason}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        sys.exit("usage: python -m app.services.imports <path-to-repository> [--summary]")
    try:
        result = scan_imports(args[0])
    except ValueError as exc:
        sys.exit(str(exc))
    if "--summary" in sys.argv:
        _print_summary(result)
    else:
        print(result.model_dump_json(indent=2))
