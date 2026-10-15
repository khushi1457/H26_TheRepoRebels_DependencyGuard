"""JSON shapes returned by the repository scanner.

These models are the contract between the scanner, the API and (later)
the React frontend, so field names should stay stable once the frontend
starts relying on them.
"""

from typing import Literal

from pydantic import BaseModel, Field

Scope = Literal["runtime", "dev", "optional", "build", "group", "constraint"]
ManifestKind = Literal["requirements", "pyproject", "setup.cfg", "setup.py", "pipfile"]


class DependencyDeclaration(BaseModel):
    """One place where a repository declares that it needs a package."""

    name: str = Field(description="Package name exactly as written in the file")
    normalized_name: str = Field(description="PEP 503 normalized name, used for matching")
    specifier: str = Field(description="Version constraint, e.g. '==2.31.0' or '>=7'; empty if unconstrained")
    pinned_version: str | None = Field(None, description="Set only when the constraint is a single exact pin")
    extras: list[str] = []
    marker: str | None = Field(None, description="Environment marker, e.g. python_version < '3.11'")
    scope: Scope
    group: str | None = Field(None, description="Extras / dependency-group name, when applicable")
    source_file: str = Field(description="Path relative to the repository root, using forward slashes")
    line: int | None = Field(None, description="1-based line number, when it can be determined")
    raw: str = Field(description="The original text of the declaration")


class UnparsedEntry(BaseModel):
    """A line or file the scanner saw but could not interpret.

    Reported instead of silently dropped, so later phases never treat
    'not understood' as 'not present'.
    """

    source_file: str
    line: int | None = None
    raw: str
    reason: str


class ManifestFile(BaseModel):
    path: str
    kind: ManifestKind
    parsed: bool
    note: str | None = None


class ManifestScanResult(BaseModel):
    manifests: list[ManifestFile]
    declarations: list[DependencyDeclaration]
    unparsed: list[UnparsedEntry]
