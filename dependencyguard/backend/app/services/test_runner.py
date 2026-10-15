"""Phase 5: run the target repository's own test suite in an isolated environment.

Isolation used (honest list, also shown in the capsule):
  * a throwaway copy of the repository in a temp folder
  * a fresh virtual environment (separate from DependencyGuard's)
  * a hard timeout on every command
It is NOT a container or VM; only run repositories you are willing to execute.

Baseline   = dependencies exactly as declared.
Candidate  = the same, with ONE package moved to its latest version (a drop-in
             upgrade, no code changes). Comparing the two is observed evidence.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

INSTALL_TIMEOUT_S = 300
TEST_TIMEOUT_S = 180
_SUMMARY_RE = re.compile(r"(\d+) (passed|failed|error|errors|skipped|xfailed|xpassed)")


def _env(cwd: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH")}
    env.update({"PYTHONPATH": str(cwd), "PIP_DISABLE_PIP_VERSION_CHECK": "1"})
    return env


def _run(cmd: list[str], cwd: Path, timeout: int) -> tuple[int, str, float]:
    start = time.monotonic()
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                              env=_env(cwd))
        return proc.returncode, (proc.stdout + proc.stderr), time.monotonic() - start
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"").decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return -1, out + f"\n[timed out after {timeout}s]", time.monotonic() - start


def _parse(output: str) -> dict:
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    tail = output.strip().splitlines()[-1:] if output.strip() else []
    for n, word in _SUMMARY_RE.findall(" ".join(tail)):
        key = "errors" if word.startswith("error") else word
        if key in counts:
            counts[key] = int(n)
    failed_tests = sorted(set(re.findall(r"^(?:FAILED|ERROR) (\S+)", output, re.MULTILINE)))
    return {**counts, "failed_tests": failed_tests}


class Sandbox:
    def __init__(self, repo: Path, requirement_files: list[str]):
        self.tmp = Path(tempfile.mkdtemp(prefix="depguard-"))
        self.src = self.tmp / "repo"
        shutil.copytree(repo, self.src, ignore=shutil.ignore_patterns(".git", ".venv", "venv", "__pycache__"))
        self.venv = self.tmp / "venv"
        bindir = "Scripts" if sys.platform == "win32" else "bin"
        self.py = str(self.venv / bindir / ("python.exe" if sys.platform == "win32" else "python"))
        self.requirement_files = requirement_files
        self.log: list[dict] = []

    def setup(self) -> tuple[bool, str]:
        code, out, secs = _run([sys.executable, "-m", "venv", str(self.venv)], self.tmp, 120)
        if code != 0:
            return False, out[-2000:]
        args = [self.py, "-m", "pip", "install", "-q"]
        for f in self.requirement_files:
            args += ["-r", f]
        if "pytest" not in " ".join((self.src / f).read_text(errors="ignore") for f in self.requirement_files):
            args.append("pytest")
        code, out, secs = _run(args, self.src, INSTALL_TIMEOUT_S)
        self.log.append({"step": "install declared dependencies", "command": " ".join(args[1:]),
                         "exit_code": code, "seconds": round(secs, 1)})
        return code == 0, out[-3000:]

    def pip(self, spec: str) -> tuple[bool, str]:
        code, out, secs = _run([self.py, "-m", "pip", "install", "-q", spec], self.src, INSTALL_TIMEOUT_S)
        return code == 0, out[-2000:]

    def pytest(self, label: str) -> dict:
        cmd = [self.py, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
        code, out, secs = _run(cmd, self.src, TEST_TIMEOUT_S)
        return {"label": label, "command": "python -m pytest -q", "exit_code": code,
                "seconds": round(secs, 1), **_parse(out), "output_tail": out[-2500:]}

    def installed_version(self, dist: str) -> str | None:
        code, out, _ = _run([self.py, "-m", "pip", "show", dist], self.src, 60)
        m = re.search(r"^Version: (\S+)", out, re.MULTILINE)
        return m.group(1) if m else None

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def run_tests(repo: Path, requirement_files: list[str], candidates: list[tuple[str, str]]) -> dict:
    """candidates: [(distribution, version_to_try), ...]"""
    if not requirement_files:
        return {"executed": False, "reason": "No requirements files found to build a test environment."}
    box = Sandbox(repo, requirement_files)
    try:
        ok, out = box.setup()
        if not ok:
            return {"executed": False, "reason": "Installing the declared dependencies failed.",
                    "install_output_tail": out, "log": box.log}
        baseline = box.pytest("baseline (declared versions)")
        runs = []
        for dist, version in candidates:
            original = box.installed_version(dist)
            ok, out = box.pip(f"{dist}=={version}")
            if not ok:
                runs.append({"distribution": dist, "tried_version": version, "installed": False,
                             "install_output_tail": out})
                continue
            result = box.pytest(f"{dist}=={version}")
            newly_failing = sorted(set(result["failed_tests"]) - set(baseline["failed_tests"]))
            newly_passing = sorted(set(baseline["failed_tests"]) - set(result["failed_tests"]))
            runs.append({"distribution": dist, "original_version": original, "tried_version": version,
                         "installed": True, "result": result,
                         "newly_failing": newly_failing, "newly_passing": newly_passing})
            if original:
                box.pip(f"{dist}=={original}")   # restore before the next candidate
        return {"executed": True, "python": sys.version.split()[0], "isolation":
                "temp copy + separate venv + timeouts (not a container)",
                "baseline": baseline, "candidates": runs, "log": box.log}
    finally:
        box.cleanup()
