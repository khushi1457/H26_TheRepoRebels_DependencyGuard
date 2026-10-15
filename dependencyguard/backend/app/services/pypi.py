"""Observed package metadata from the public PyPI JSON API (free, no key).

Everything returned here is a fact read from PyPI at scan time. If PyPI cannot
be reached, the result says so instead of guessing.
"""

import json
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

PYPI_URL = "https://pypi.org/pypi/{name}/json"
TIMEOUT_S = 8


def _upload_date(files: list[dict]) -> str | None:
    dates = [f.get("upload_time_iso_8601") for f in files if f.get("upload_time_iso_8601")]
    return min(dates) if dates else None


def fetch(name: str, pinned: str | None, target_python: str = "3.12") -> dict:
    try:
        req = urllib.request.Request(PYPI_URL.format(name=name),
                                     headers={"User-Agent": "DependencyGuard/0.1"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            data = json.load(resp)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return {"name": name, "available": False, "error": f"PyPI lookup failed: {exc}"}

    info, releases = data.get("info", {}), data.get("releases", {})
    latest = info.get("version")
    result = {
        "name": name,
        "available": True,
        "latest_version": latest,
        "latest_release_date": _upload_date(releases.get(latest, [])),
        "pinned_version": pinned,
        "pinned_release_date": None,
        "pinned_yanked": False,
        "pinned_requires_python": None,
        "pinned_supports_target_python": None,
        "inactive_classifier": any("7 - Inactive" in c for c in info.get("classifiers") or []),
        "project_url": info.get("project_url") or f"https://pypi.org/project/{name}/",
    }
    if pinned and pinned in releases:
        files = releases[pinned]
        result["pinned_release_date"] = _upload_date(files)
        result["pinned_yanked"] = any(f.get("yanked") for f in files)
        rp = next((f.get("requires_python") for f in files if f.get("requires_python")), None)
        result["pinned_requires_python"] = rp
        if rp:
            try:
                result["pinned_supports_target_python"] = Version(target_python + ".0") in SpecifierSet(rp)
            except (InvalidSpecifier, InvalidVersion):
                pass
    return result


def fetch_many(items: list[tuple[str, str | None]]) -> dict[str, dict]:
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = pool.map(lambda it: fetch(*it), items)
    return {r["name"]: r for r in results}


def years_since(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        then = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - then).days / 365.25
