"""JSON shapes for package matching, usage locations and the combined scan (Phase 2, step 3)."""

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.imports import ImportScanResult
from app.schemas.scan import DependencyDeclaration, ManifestScanResult

UsageKind = Literal["call", "reference", "assignment", "base_class", "decorator"]
PackageStatus = Literal["used_and_declared", "used_not_declared", "declared_not_imported"]
MappingMethod = Literal["exact_name", "known_alias", "unresolved", "not_imported"]


class Usage(BaseModel):
    """One place where code touches a third-party name."""

    symbol: str = Field(description="Fully qualified name, e.g. 'requests.exceptions.ConnectionError'")
    local_name: str = Field(description="What the code actually wrote, e.g. 'S' for 'from requests import Session as S'")
    kind: UsageKind = Field(description="call / reference / assignment (writing to a third-party "
                                        "attribute, e.g. a monkeypatch) / base_class / decorator")
    file: str
    line: int
    column: int
    code: str = Field(description="The source line, stripped; used later as context for Gemma")


class Finding(BaseModel):
    """A fact computed directly from the files. Not a guess and not AI output."""

    code: str = Field(description="Stable machine-readable id, e.g. 'incompatible_declarations'")
    message: str


class PackageReport(BaseModel):
    distribution: str = Field(description="PyPI distribution name (normalized), or the import "
                                          "name when it could not be matched")
    import_names: list[str]
    mapping_method: MappingMethod
    status: PackageStatus
    declared_in: list[DependencyDeclaration]
    import_count: int
    used_in_runtime_code: bool
    used_in_tests: bool
    usages: list[Usage]
    findings: list[Finding]


class RepositoryScan(BaseModel):
    """Everything Phase 2 knows about a repository, in one object."""

    packages: list[PackageReport]
    manifests: ManifestScanResult
    imports: ImportScanResult
    limitations: list[str]
