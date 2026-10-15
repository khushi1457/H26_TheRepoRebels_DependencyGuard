"""Phase 6: the Dependency Exit Capsule.

Every section is labelled with where its content came from:
  OBSERVED    - read from the repository files or PyPI, or produced by running tests
  AI-GENERATED - Gemma 4 output; NOT verified unless a matching test run says so
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from app.config import settings
from app.services import gemma, risk
from app.services.pypi import fetch_many
from app.services.repo_input import resolve_repo
from app.services.scanner import scan_repository
from app.services.test_runner import run_tests

MAX_TEST_CANDIDATES = 5


def _pinned(pkg: dict) -> str | None:
    pins = [d["pinned_version"] for d in pkg["declared_in"]
            if d["pinned_version"] and d["scope"] in ("runtime", "dev")]
    req = [d["pinned_version"] for d in pkg["declared_in"]
           if d["pinned_version"] and d["source_file"].endswith(".txt")]
    return (req or pins or [None])[0]


def analyse(source: str) -> tuple[dict, list[dict]]:
    """Scan + PyPI metadata + observed risk. Fast; no AI, no test execution."""
    root = resolve_repo(source)
    scan = scan_repository(str(root)).model_dump()
    meta = fetch_many([(p["distribution"], _pinned(p)) for p in scan["packages"]])
    for p in scan["packages"]:
        p["pypi"] = meta.get(p["distribution"])
        p["risk_indicators"] = risk.indicators_for(p, p["pypi"])
        p["risk_level"] = risk.level(p["risk_indicators"])
    scan["packages"].sort(key=lambda p: -risk.SEVERITY_ORDER[p["risk_level"]])
    return scan, [str(root)]


def build_capsule(source: str, use_gemma: bool = True, run_test_suite: bool = False) -> dict:
    scan, (root,) = analyse(source)
    at_risk = [p for p in scan["packages"] if p["risk_level"] in ("high", "medium")]

    ai_by_dist: dict[str, dict] = {}
    if use_gemma and at_risk:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = pool.map(lambda p: gemma.recommend(p, p["pypi"], p["risk_indicators"]), at_risk)
        ai_by_dist = {p["distribution"]: r for p, r in zip(at_risk, results)}

    tests: dict = {"executed": False, "reason": "Test execution was not requested."}
    if run_test_suite:
        req_files = [m["path"] for m in scan["manifests"]["manifests"]
                     if m["kind"] == "requirements" and m["parsed"] and m.get("note") != "constraints file"]
        candidates = []
        for p in at_risk:
            meta = p.get("pypi") or {}
            if p["declared_in"] and meta.get("latest_version") and meta.get("latest_version") != _pinned(p):
                candidates.append((p["distribution"], meta["latest_version"]))
        from pathlib import Path
        tests = run_tests(Path(root), req_files, candidates[:MAX_TEST_CANDIDATES])

    packages = []
    for p in scan["packages"]:
        dist = p["distribution"]
        ai = ai_by_dist.get(dist)
        cand = next((c for c in tests.get("candidates", []) if c["distribution"] == dist), None)
        gaps_observed = []
        if cand and cand.get("installed"):
            for t in cand["newly_failing"]:
                gaps_observed.append(f"{t} passes with the declared version but FAILS with {dist}=={cand['tried_version']}")
            for t in cand["newly_passing"]:
                gaps_observed.append(f"{t} fails with the declared version but PASSES with {dist}=={cand['tried_version']}")
        rec = (ai or {}).get("recommendation") or {}
        open_concerns = list(rec.get("open_questions", []))
        if p["risk_level"] in ("high", "medium") and not cand:
            open_concerns.append("No drop-in upgrade test was executed for this package.")
        if rec.get("code_changes"):
            open_concerns.append("Gemma's proposed code changes were NOT applied or tested automatically.")
        packages.append({
            "dependency": {"distribution": dist, "import_names": p["import_names"], "status": p["status"],
                           "declared_in": [{k: d[k] for k in ("source_file", "line", "specifier", "scope")}
                                           for d in p["declared_in"]],
                           "pypi": p["pypi"]},
            "risk_level": p["risk_level"],
            "risk_indicators_OBSERVED": p["risk_indicators"],
            "usage_locations_OBSERVED": [{k: u[k] for k in ("file", "line", "kind", "symbol", "code")}
                                         for u in p["usages"]],
            "ai_recommendation_UNVERIFIED": ({"model": ai["model"], **rec} if ai and ai.get("ok") else
                                             {"error": ai["error"]} if ai else None),
            "executed_tests_OBSERVED": cand,
            "compatibility_gaps": {"observed": gaps_observed,
                                   "ai_predicted_unverified": rec.get("compatibility_warnings", [])},
            "open_concerns": open_concerns,
        })

    model = next((a["model"] for a in ai_by_dist.values() if a.get("ok")), None)
    capsule = {
        "capsule_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generated_by": f"{settings.app_name} {settings.version}",
        "repository": source,
        "ai_model": {"name": model, "provider": "Google Gemini API (Gemma 4 open-weight model)",
                     "role": "replacement and migration recommendations only"} if model else None,
        "summary": {
            "packages": len(packages),
            "high_risk": sum(p["risk_level"] == "high" for p in packages),
            "medium_risk": sum(p["risk_level"] == "medium" for p in packages),
            "tests_executed": tests.get("executed", False),
            "baseline": {k: tests["baseline"][k] for k in ("passed", "failed", "errors", "failed_tests")}
            if tests.get("executed") else None,
        },
        "test_execution_OBSERVED": {k: v for k, v in tests.items() if k != "candidates"},
        "packages": packages,
        "parse_failures": scan["imports"]["parse_failures"],
        "unparsed_declarations": scan["manifests"]["unparsed"],
        "limitations": scan["limitations"] + [
            "Test isolation is a temp copy + separate venv + timeouts, not a container.",
            "Candidate test runs are drop-in version upgrades only; replacements needing code changes are not executed.",
            "AI recommendations are generated per package and are not verified unless a test run above confirms them.",
        ],
    }
    capsule["markdown"] = to_markdown(capsule)
    return capsule


def to_markdown(c: dict) -> str:
    L = [f"# Dependency Exit Capsule", "",
         f"- **Repository:** {c['repository']}", f"- **Generated:** {c['generated_at']} by {c['generated_by']}",
         f"- **AI model:** {c['ai_model']['name'] if c['ai_model'] else 'not used'}", "",
         "> Sections marked **OBSERVED** come from the repository files, PyPI, or executed tests.",
         "> Sections marked **AI-GENERATED (unverified)** are Gemma 4 suggestions and have not been verified.", ""]
    s = c["summary"]
    L += ["## Summary", "", f"- Packages analysed: {s['packages']}",
          f"- High risk: {s['high_risk']} · Medium risk: {s['medium_risk']}",
          f"- Tests executed: {'yes' if s['tests_executed'] else 'no'}"]
    if s["baseline"]:
        b = s["baseline"]
        L.append(f"- Baseline test run: {b['passed']} passed, {b['failed']} failed, {b['errors']} errors")
        for t in b["failed_tests"]:
            L.append(f"  - failing at baseline: `{t}`")
    elif not s["tests_executed"]:
        L.append(f"- Reason: {c['test_execution_OBSERVED'].get('reason', '')}")
    L.append("")
    for p in c["packages"]:
        d = p["dependency"]
        L += [f"## {d['distribution']} — risk: {p['risk_level'].upper()}", ""]
        L += ["### Dependency details (OBSERVED)", f"- Status: {d['status']}",
              f"- Import names: {', '.join(d['import_names']) or '—'}"]
        for x in d["declared_in"]:
            L.append(f"- Declared in `{x['source_file']}:{x['line']}` as `{x['specifier'] or 'any version'}` ({x['scope']})")
        if d["pypi"] and d["pypi"].get("available"):
            m = d["pypi"]
            L.append(f"- PyPI: latest {m['latest_version']} ({(m['latest_release_date'] or '')[:10]}); "
                     f"pinned {m['pinned_version'] or '—'} ({(m['pinned_release_date'] or '')[:10] or '—'})")
        L += ["", "### Risk indicators (OBSERVED)"]
        L += [f"- [{i['severity']}] {i['message']} _(source: {i['source']})_" for i in p["risk_indicators_OBSERVED"]] or ["- none"]
        L += ["", "### Usage locations (OBSERVED)"]
        L += [f"- `{u['file']}:{u['line']}` {u['kind']} `{u['symbol']}` — `{u['code']}`"
              for u in p["usage_locations_OBSERVED"]] or ["- no usages found"]
        ai = p["ai_recommendation_UNVERIFIED"]
        if ai:
            L += ["", "### Proposed replacement & migration (AI-GENERATED, unverified)"]
            if "error" in ai:
                L.append(f"- Gemma request failed: {ai['error']}")
            else:
                L += [f"- Model: {ai.get('model')} · Confidence (self-reported): {ai.get('confidence')}",
                      f"- Action: **{ai.get('action')}** → `{ai.get('replacement')}`", f"- Rationale: {ai.get('rationale')}",
                      "", "Migration steps:"]
                L += [f"{n}. {step}" for n, step in enumerate(ai.get("migration_steps", []), 1)]
                if ai.get("code_changes"):
                    L += ["", "Proposed code changes:"]
                    for ch in ai["code_changes"]:
                        L += [f"- `{ch.get('file')}:{ch.get('line')}`", f"  - before: `{ch.get('before')}`",
                              f"  - after: `{ch.get('after')}`"]
                L += ["", "Suggested tests:"] + [f"- {t}" for t in ai.get("suggested_tests", [])]
        t = p["executed_tests_OBSERVED"]
        L += ["", "### Executed test results (OBSERVED)"]
        if t and t.get("installed"):
            r = t["result"]
            L.append(f"- With `{d['distribution']}=={t['tried_version']}` (was {t['original_version']}): "
                     f"{r['passed']} passed, {r['failed']} failed, {r['errors']} errors")
        elif t:
            L.append(f"- Could not install `{d['distribution']}=={t['tried_version']}`")
        else:
            L.append("- not executed for this package")
        g = p["compatibility_gaps"]
        L += ["", "### Compatibility gaps"]
        L += [f"- OBSERVED: {x}" for x in g["observed"]]
        L += [f"- AI-predicted (unverified): {x}" for x in g["ai_predicted_unverified"]]
        if not g["observed"] and not g["ai_predicted_unverified"]:
            L.append("- none identified")
        L += ["", "### Open concerns"] + ([f"- {x}" for x in p["open_concerns"]] or ["- none"]) + [""]
    L += ["## Limitations", ""] + [f"- {x}" for x in c["limitations"]]
    return "\n".join(L) + "\n"
