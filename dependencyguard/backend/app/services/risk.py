"""Phase 3: risk indicators built ONLY from observed evidence.

Sources: the repository scan (files/lines) and PyPI metadata fetched at scan time.
Each indicator says where its evidence came from. No AI is used here.
"""

from packaging.version import InvalidVersion, Version

from app.services.pypi import years_since

SEVERITY_ORDER = {"high": 3, "medium": 2, "low": 1, "none": 0}
STALE_YEARS = 2.0


def _major(v: str | None) -> int | None:
    try:
        return Version(v).major if v else None
    except InvalidVersion:
        return None


def indicators_for(package: dict, meta: dict | None) -> list[dict]:
    out: list[dict] = []

    def add(code, severity, message, source):
        out.append({"code": code, "severity": severity, "message": message, "source": source})

    for f in package["findings"]:
        sev = {"incompatible_declarations": "high", "undeclared_import": "medium",
               "private_api": "medium", "monkeypatch": "medium",
               "inconsistent_declarations": "medium", "not_imported": "low"}.get(f["code"], "low")
        if f["code"] == "inconsistent_declarations" and any(
                x["code"] == "incompatible_declarations" for x in package["findings"]):
            continue  # already covered by the stronger finding
        add(f["code"], sev, f["message"], "repository scan")

    if meta and meta.get("available"):
        pinned, latest = meta.get("pinned_version"), meta.get("latest_version")
        pm, lm = _major(pinned), _major(latest)
        if pm is not None and lm is not None and lm > pm:
            add("major_versions_behind", "high" if lm - pm >= 2 else "medium",
                f"Pinned {pinned}; latest on PyPI is {latest} ({lm - pm} major version(s) behind).",
                "PyPI metadata")
        age = years_since(meta.get("latest_release_date"))
        if age is not None and age >= STALE_YEARS:
            add("no_recent_release", "high" if age >= 4 else "medium",
                f"Latest release {latest} was published {age:.1f} years ago "
                f"({meta['latest_release_date'][:10]}); the project may be unmaintained.",
                "PyPI metadata")
        if meta.get("inactive_classifier"):
            add("marked_inactive", "high", "PyPI classifiers mark the project 'Development Status :: 7 - Inactive'.",
                "PyPI metadata")
        if meta.get("pinned_yanked"):
            add("pinned_version_yanked", "high", f"Pinned version {pinned} has been yanked on PyPI.", "PyPI metadata")
        if meta.get("pinned_supports_target_python") is False:
            add("python_incompatible", "high",
                f"Pinned {pinned} declares requires_python '{meta['pinned_requires_python']}', "
                "which excludes Python 3.12.", "PyPI metadata")
    elif meta:
        add("metadata_unavailable", "low", meta.get("error", "PyPI metadata unavailable"), "PyPI metadata")

    return out


def level(indicators: list[dict]) -> str:
    return max((i["severity"] for i in indicators), key=SEVERITY_ORDER.get, default="none")
