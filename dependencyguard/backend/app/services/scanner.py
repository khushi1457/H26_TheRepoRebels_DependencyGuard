"""Phase 2 entry point: one call that scans a repository end to end.

    manifests  -> what the repo says it needs
    imports    -> what the code actually imports
    package_map-> which import belongs to which declared package
    usages     -> where each package is used, line by line

Every 'finding' here is computed from the files. Nothing is guessed or AI-generated.

Run directly:
    python -m app.services.scanner ../../LinkWatch --summary
"""

import re
import sys
from collections import defaultdict

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from app.schemas.packages import Finding, PackageReport, RepositoryScan
from app.services.imports import scan_imports
from app.services.manifests import scan_manifests
from app.services.package_map import resolve
from app.services.repo_files import resolve_root
from app.services.usages import find_usages

_TEST_PATH_RE = re.compile(r"(^|/)(tests?|testing)(/|$)|(^|/)(test_[^/]*|[^/]*_test|conftest)\.py$")

LIMITATIONS = [
    "Static analysis only: repository code is never imported or executed.",
    "Dynamic imports (importlib.import_module, __import__) are not detected.",
    "Calls on object instances are not traced (no type inference).",
    "'from x import *' imports are recorded but their usages cannot be followed.",
    "Import-to-package matching uses exact names plus a small table of well-known aliases; "
    "anything else is reported as 'unresolved'.",
    "Dependency scope (runtime/dev) for requirements files is inferred from the file name.",
]


def _is_test_file(path: str) -> bool:
    return bool(_TEST_PATH_RE.search(path))


def _declaration_findings(decls) -> list[Finding]:
    """Compare declarations of the same package across files."""
    findings: list[Finding] = []
    relevant = [d for d in decls if d.scope in ("runtime", "constraint")]
    if len({(d.specifier or "any") for d in relevant}) > 1:
        where = "; ".join(f"{d.source_file}:{d.line} '{d.specifier or 'any version'}'" for d in relevant)
        findings.append(Finding(code="inconsistent_declarations",
                                message=f"Declared with different version rules: {where}"))
    for pinned in [d for d in relevant if d.pinned_version]:
        for other in relevant:
            if other is pinned or not other.specifier:
                continue
            try:
                if Version(pinned.pinned_version) not in SpecifierSet(other.specifier):
                    findings.append(Finding(
                        code="incompatible_declarations",
                        message=(f"{pinned.source_file}:{pinned.line} pins {pinned.pinned_version}, which "
                                 f"{other.source_file}:{other.line} ('{other.specifier}') does not allow. "
                                 "Installing from each file gives a different, incompatible version.")))
            except (InvalidSpecifier, InvalidVersion):
                continue  # e.g. Poetry '^1.2' syntax; not PEP 440
    return findings


def scan_repository(root_path: str) -> RepositoryScan:
    root = resolve_root(root_path)
    manifests = scan_manifests(str(root))
    imports = scan_imports(str(root))

    decls_by_name = defaultdict(list)
    for d in manifests.declarations:
        decls_by_name[d.normalized_name].append(d)
    declared = set(decls_by_name)

    third_party = [o for o in imports.occurrences if o.origin == "third_party"]
    dist_of_import: dict[str, tuple[str, str]] = {
        top: resolve(top, declared) for top in {o.top_level for o in third_party}
    }

    occ_by_file = defaultdict(list)
    for o in third_party:
        occ_by_file[o.file].append(o)
    usages_by_dist = defaultdict(list)
    for file, occs in occ_by_file.items():
        for usage in find_usages(root, file, occs):
            top = usage.symbol.split(".")[0]
            if top in dist_of_import:
                usages_by_dist[dist_of_import[top][0]].append(usage)

    imports_by_dist = defaultdict(list)
    method_of_dist: dict[str, str] = {}
    for o in third_party:
        dist, method = dist_of_import[o.top_level]
        imports_by_dist[dist].append(o)
        method_of_dist[dist] = method

    packages: list[PackageReport] = []
    for dist in sorted(set(imports_by_dist) | declared):
        occs = imports_by_dist.get(dist, [])
        decls = decls_by_name.get(dist, [])
        files = {o.file for o in occs}
        findings = _declaration_findings(decls)

        if occs and decls:
            status = "used_and_declared"
        elif occs:
            status = "used_not_declared"
            findings.append(Finding(code="undeclared_import", message=(
                f"Imported in {', '.join(sorted(files))} but not declared in any dependency file. "
                "It only works if another package happens to install it.")))
        else:
            status = "declared_not_imported"
            findings.append(Finding(code="not_imported", message=(
                "Declared but never imported. It may still be needed (CLI tool, plugin, build backend, "
                "or a pin for a transitive dependency); confirm before removing.")))

        for o in occs:
            if o.private_names:
                findings.append(Finding(code="private_api", message=(
                    f"{o.file}:{o.line} imports private name(s) {', '.join(o.private_names)} from "
                    f"'{o.module}'; internal APIs can change or disappear in any release.")))
        for u in usages_by_dist.get(dist, []):
            if u.kind == "assignment":
                findings.append(Finding(code="monkeypatch", message=(
                    f"{u.file}:{u.line} overwrites '{u.symbol}' at runtime; upgrades that change "
                    "this attribute's signature will break silently.")))

        runtime_files = {f for f in files if not _is_test_file(f)}
        packages.append(PackageReport(
            distribution=dist,
            import_names=sorted({o.top_level for o in occs}),
            mapping_method=method_of_dist.get(dist, "not_imported"),
            status=status,
            declared_in=decls,
            import_count=len(occs),
            used_in_runtime_code=bool(runtime_files),
            used_in_tests=bool(files - runtime_files),
            usages=sorted(usages_by_dist.get(dist, []), key=lambda u: (u.file, u.line, u.column)),
            findings=findings,
        ))

    return RepositoryScan(packages=packages, manifests=manifests, imports=imports, limitations=LIMITATIONS)


def _print_summary(scan: RepositoryScan) -> None:
    for p in scan.packages:
        imp = f" (import {', '.join(p.import_names)})" if p.import_names and p.import_names != [p.distribution] else ""
        where = "runtime" if p.used_in_runtime_code else ("tests" if p.used_in_tests else "-")
        print(f"\n{p.distribution}{imp}  [{p.status}, via {p.mapping_method}, used in: {where}]")
        for d in p.declared_in:
            print(f"   declared  {d.source_file}:{d.line}  {d.specifier or 'any'}  ({d.scope})")
        for u in p.usages:
            print(f"   used      {u.file}:{u.line:<4} {u.kind:<10} {u.symbol}")
        for f in p.findings:
            print(f"   ! {f.code}: {f.message}")
    print(f"\nParse failures: {len(scan.imports.parse_failures)}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        sys.exit("usage: python -m app.services.scanner <path-to-repository> [--summary]")
    try:
        result = scan_repository(args[0])
    except ValueError as exc:
        sys.exit(str(exc))
    if "--summary" in sys.argv:
        _print_summary(result)
    else:
        print(result.model_dump_json(indent=2))
