"""Find where each third-party name is used, following aliases.

Given   from requests import Session as S
then    S()                         -> requests.Session        (call)
and     requests.Session.request = f -> requests.Session.request (assignment, i.e. a monkeypatch)

Limits (static analysis, no type inference):
  * calls on instances are not traced (session = S(); session.get(...) records only S())
  * 'from x import *' cannot be followed
  * a local variable that shadows an imported name is not detected
"""

import ast
from pathlib import Path

from app.schemas.imports import ImportOccurrence
from app.schemas.packages import Usage


def _dotted(node: ast.AST) -> list[str] | None:
    """['requests', 'exceptions', 'Timeout'] for requests.exceptions.Timeout, else None."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return list(reversed(parts))
    return None


def build_alias_map(occurrences: list[ImportOccurrence]) -> dict[str, str]:
    """Local name in one file -> fully qualified third-party name."""
    aliases: dict[str, str] = {}
    for occ in occurrences:
        if occ.statement == "import":
            if occ.module_alias:                       # import numpy as np
                aliases[occ.module_alias] = occ.module
            else:                                       # import a.b.c binds 'a'
                aliases[occ.top_level] = occ.top_level
        else:
            for n in occ.names:
                if n.name != "*":                       # from a import b as c
                    aliases[n.alias or n.name] = f"{occ.module}.{n.name}"
    return aliases


class _UsageVisitor(ast.NodeVisitor):
    def __init__(self, aliases: dict[str, str], lines: list[str], source_file: str):
        self.aliases = aliases
        self.lines = lines
        self.source_file = source_file
        self.found: list[Usage] = []

    def _resolve(self, node: ast.AST) -> tuple[str, str] | None:
        parts = _dotted(node)
        if not parts or parts[0] not in self.aliases:
            return None
        return ".".join([self.aliases[parts[0]], *parts[1:]]), ".".join(parts)

    def _add(self, node: ast.AST, kind: str) -> bool:
        hit = self._resolve(node)
        if not hit:
            return False
        symbol, local = hit
        line = self.lines[node.lineno - 1].strip() if node.lineno <= len(self.lines) else ""
        self.found.append(Usage(symbol=symbol, local_name=local, kind=kind, file=self.source_file,
                                line=node.lineno, column=node.col_offset, code=line[:200]))
        return True

    # Import statements define names; they are not usages.
    def visit_Import(self, node):
        pass

    def visit_ImportFrom(self, node):
        pass

    def _visit_decorated(self, node):
        for deco in node.decorator_list:
            target = deco.func if isinstance(deco, ast.Call) else deco
            if self._add(target, "decorator"):
                if isinstance(deco, ast.Call):
                    for arg in [*deco.args, *deco.keywords]:
                        self.visit(arg)
            else:
                self.visit(deco)
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                if not self._add(base, "base_class"):
                    self.visit(base)
            for kw in node.keywords:
                self.visit(kw)
        else:
            self.visit(node.args)
            if node.returns:
                self.visit(node.returns)
        for stmt in node.body:
            self.visit(stmt)

    visit_FunctionDef = _visit_decorated
    visit_AsyncFunctionDef = _visit_decorated
    visit_ClassDef = _visit_decorated

    def visit_Call(self, node: ast.Call):
        if not self._add(node.func, "call"):
            self.visit(node.func)
        for arg in [*node.args, *node.keywords]:
            self.visit(arg)

    def visit_Attribute(self, node: ast.Attribute):
        kind = "assignment" if isinstance(node.ctx, (ast.Store, ast.Del)) else "reference"
        if not self._add(node, kind):
            self.generic_visit(node)

    def visit_Name(self, node: ast.Name):
        kind = "assignment" if isinstance(node.ctx, (ast.Store, ast.Del)) else "reference"
        if kind == "reference":           # rebinding a local name is not a use of the package
            self._add(node, kind)


def find_usages(root: Path, source_file: str, occurrences: list[ImportOccurrence]) -> list[Usage]:
    """Usages of third-party names in one file. `occurrences` are that file's third-party imports."""
    aliases = build_alias_map(occurrences)
    if not aliases:
        return []
    path = root / source_file
    try:
        raw = path.read_bytes()
        tree = ast.parse(raw, filename=source_file)
    except (SyntaxError, ValueError, OSError):
        return []  # already reported by the import scanner
    lines = raw.decode("utf-8", errors="replace").splitlines()
    visitor = _UsageVisitor(aliases, lines, source_file)
    visitor.visit(tree)
    return visitor.found
