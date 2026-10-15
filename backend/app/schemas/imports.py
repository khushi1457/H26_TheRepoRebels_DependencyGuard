"""JSON shapes for the import scanner (Phase 2, step 2)."""

from typing import Literal

from pydantic import BaseModel, Field

ImportOrigin = Literal["stdlib", "first_party", "third_party"]


class ImportedName(BaseModel):
    name: str = Field(description="Name pulled in by 'from x import name'; '*' for star imports")
    alias: str | None = Field(None, description="Local name when 'as' is used")


class ImportOccurrence(BaseModel):
    """One import statement, exactly where it appears in the repository."""

    module: str = Field(description="Full dotted module, e.g. 'requests.utils'")
    top_level: str = Field(description="First segment, e.g. 'requests'; used for package matching")
    origin: ImportOrigin
    statement: Literal["import", "from"]
    names: list[ImportedName] = Field(description="Empty for 'import x' statements")
    module_alias: str | None = Field(None, description="Set for 'import x as y'")
    file: str = Field(description="Path relative to the repository root, forward slashes")
    line: int
    column: int
    inside_function: bool = Field(description="Import happens lazily, only when a function runs")
    inside_try: bool = Field(description="Wrapped in try/except, often an optional dependency")
    type_checking_only: bool = Field(description="Under 'if TYPE_CHECKING:'; never imported at runtime")
    private_names: list[str] = Field(
        description="Underscore names taken from the module (e.g. _parse_content_type_header); "
                    "these are internal APIs the package does not promise to keep")


class ParseFailure(BaseModel):
    """A .py file the scanner could not parse. Reported, never silently skipped."""

    file: str
    line: int | None = None
    reason: str


class ImportSummary(BaseModel):
    top_level: str
    occurrences: int
    files: list[str]


class ImportScanResult(BaseModel):
    python_files_scanned: int
    first_party_modules: list[str] = Field(description="Top-level names treated as the repo's own code")
    third_party: list[ImportSummary]
    occurrences: list[ImportOccurrence]
    parse_failures: list[ParseFailure]
