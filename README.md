# DependencyGuard

**Team TheRepoRebels · Hacktoberfest Hack Day Mumbai 2026 · Track 1: Gemma 4 – Dependency Resilience**

DependencyGuard is an open-source developer tool that scans a Python repository, finds risky dependencies
and exactly where they are used, asks **Gemma 4** for a replacement and migration plan, runs the
repository's own tests against the original and upgraded dependencies, and packages everything into a
reusable **Dependency Exit Capsule**.

Every result is labelled **OBSERVED** (read from files, PyPI or executed tests) or
**AI-GENERATED (unverified)** (Gemma 4 output). An AI suggestion is never presented as verified.

## 1. Problem and solution overview

Maintainers usually find dependency problems only after something breaks. DependencyGuard makes that
visible in advance on the organizer-provided repository (https://github.com/rishrek/LinkWatch) or any
other Python repository:

1. **Scan** dependency files (`requirements*.txt`, `pyproject.toml` incl. Poetry, `setup.cfg`) and every
   `import` (Python AST, no code execution), then match imports to packages and record each usage
   (file, line, call/reference/decorator/base class/monkeypatch).
2. **Assess risk from evidence only:** conflicting declarations across files, undeclared imports,
   private-API use, monkeypatches, major versions behind, no recent release, yanked or
   Python-incompatible pins (PyPI metadata fetched at scan time).
3. **Recommend with Gemma 4:** for each medium/high-risk package, Gemma 4 receives the evidence and the
   real usage lines and returns structured JSON: action, replacement, migration steps, code changes,
   compatibility warnings, suggested tests, open questions.
4. **Verify by execution (optional):** the target's own test suite runs in a throwaway venv with the
   declared versions (baseline), then once per at-risk package upgraded to its latest version.
   Newly failing / newly passing tests are reported as observed compatibility gaps.
5. **Exit Capsule:** Markdown + JSON with dependency details, risk indicators, usage locations, proposed
   replacement, migration steps, suggested tests, executed test results, compatibility gaps,
   open concerns and limitations.

## 2. Setup

Requires Python **3.12** (LinkWatch does not support 3.14) and Git.

```bash
git clone <this repo>
cd <this repo>/dependencyguard
bash setup_mac.sh            # macOS/Linux: creates .venv and installs requirements
# Windows: py -3.12 -m venv .venv ; .\.venv\Scripts\Activate.ps1 ; pip install -r requirements.txt
```

Create `dependencyguard/.env` (gitignored, never commit it):

```
GEMINI_API_KEY=your-google-ai-studio-key
# optional: pin an exact model id instead of auto-selecting the largest available Gemma 4 model
# GEMMA_MODEL=gemma-4-...
```

## 3. Usage

```bash
source .venv/bin/activate
cd backend
uvicorn app.main:app --reload
```

- Web UI: http://127.0.0.1:8000 → enter a GitHub URL or local path → **Quick scan** or **Generate Exit Capsule**
  (tick *Run tests* for executed evidence; takes a few minutes) → download the capsule (.md / .json).
- API docs: http://127.0.0.1:8000/docs (`GET /api/scan`, `POST /api/capsule`, `GET /api/capsule/latest.md|json`).
- CLI (no server): `python -m app.services.scanner ../../LinkWatch --summary`

## 4. Architecture

```
backend/app/
  main.py                FastAPI app + serves static/index.html
  routers/analysis.py    /api/scan, /api/capsule, downloads
  services/
    repo_input.py        GitHub URL -> shallow clone, or local folder
    repo_files.py        safe read-only traversal (no symlinks, skips .git/.venv)
    manifests.py         dependency file parsing (PEP 508/621/735, Poetry, setup.cfg)
    imports.py           AST import detection (stdlib / first-party / third-party, lazy/try/private flags)
    package_map.py       import name -> distribution name
    usages.py            alias-aware usage locations
    scanner.py           combined scan + computed findings
    pypi.py              observed PyPI metadata
    risk.py              evidence-based risk indicators (no AI)
    gemma.py             Gemma 4 via Gemini API, structured JSON
    test_runner.py       isolated baseline + upgrade test runs
    capsule.py           Dependency Exit Capsule (JSON + Markdown)
  static/index.html      single-page frontend (vanilla JS)
```

## 5. Dependencies

FastAPI, Uvicorn, Pydantic v2, packaging, python-dotenv (see `requirements.txt`). HTTP calls to PyPI and
the Gemini API use the Python standard library. All are open source under their own licenses
(MIT / BSD / Apache-2.0).

## 6. AI model details

| Field | Value |
|---|---|
| Model name | Gemma 4 (largest Gemma 4 model available to the key, or `GEMMA_MODEL`) |
| Model version | Recorded in every capsule under `ai_model.name` (exact model id used at run time) |
| Model source | Google DeepMind, open-weight Gemma family |
| Access method | Hosted API: Google Gemini API (`generativelanguage.googleapis.com`), free-tier AI Studio key |
| License / terms | Gemma 4 open weights released under Apache 2.0 (per the event handbook); API use subject to Google's Gemini API terms |
| Role in project | Generates replacement + migration recommendations, proposed code changes, compatibility warnings and suggested tests from observed evidence. It does **not** decide risk levels and its output is never marked verified. |

## 7. Known limitations

- Static analysis only; dynamic imports (`importlib.import_module`, `__import__`) are not detected.
- Calls on object instances are not traced (no type inference); `from x import *` usages cannot be followed.
- Import→package matching uses exact names plus a small table of well-known aliases; others are "unresolved".
- Requirements-file scope (runtime/dev) is inferred from the file name. Poetry `^`/`~` constraints are
  compared textually, not semantically.
- `setup.py` and `Pipfile` are detected but not parsed (setup.py is code and is never executed).
- Test isolation is a temp copy + separate venv + timeouts, **not** a container. Only run repositories you trust.
- Candidate test runs are drop-in version upgrades; Gemma's code changes are not applied or tested automatically.
- Gemma recommendations can be wrong; they are labelled unverified and include self-reported confidence.

## Development notes and attribution

- All code was written during the hackathon (15 Oct 2026). Commit history reflects the build order.
- AI coding assistance (Anthropic Claude) was used while writing this code; outputs were run and checked
  against LinkWatch by the team. Gemma 4 is the only model used **inside** the product.
- This repository is a fork of the organizer-provided LinkWatch repository (MIT, © its authors).
  DependencyGuard lives in `dependencyguard/`. LinkWatch's code is unchanged; its original README is kept as `LINKWATCH_README.md`.

## License

DependencyGuard is released under the MIT License (`dependencyguard/LICENSE`).
