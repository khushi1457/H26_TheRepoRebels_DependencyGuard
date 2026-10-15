"""Gemma 4 via the Gemini API (Google AI Studio key, free tier).

Key:   GEMINI_API_KEY in the project's .env file (never committed).
Model: GEMMA_MODEL in .env, or auto-selected from the models the key can access
       (largest model whose name contains 'gemma-4').

Everything returned from here is labelled AI-generated and unverified.
"""

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[3] / ".env")

API = "https://generativelanguage.googleapis.com/v1beta"
TIMEOUT_S = 120
_model_cache: str | None = None


class GemmaError(RuntimeError):
    pass


def _key() -> str:
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        raise GemmaError("GEMINI_API_KEY is not set in .env")
    return key


def _request(url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json", "x-goog-api-key": _key()})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise GemmaError(f"Gemini API HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise GemmaError(f"Gemini API unreachable: {exc}") from exc


def model_name() -> str:
    global _model_cache
    if os.getenv("GEMMA_MODEL"):
        name = os.environ["GEMMA_MODEL"]
        return name if name.startswith("models/") else f"models/{name}"
    if _model_cache:
        return _model_cache
    models = _request(f"{API}/models?pageSize=1000").get("models", [])
    gemma4 = [m["name"] for m in models
              if "gemma-4" in m["name"] and "generateContent" in m.get("supportedGenerationMethods", [])]
    if not gemma4:
        raise GemmaError("No Gemma 4 model available to this API key. Set GEMMA_MODEL in .env.")

    def size(n: str) -> float:
        m = re.search(r"(\d+(?:\.\d+)?)b", n)
        return float(m.group(1)) if m else 0.0
    _model_cache = max(gemma4, key=size)
    return _model_cache


def _extract_json(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise GemmaError("Gemma did not return JSON")
    return json.loads(text[start:end + 1])


PROMPT = """You are helping a maintainer of a Python 3.12 project prepare to move away from a risky dependency.
Use ONLY the evidence below. If evidence is insufficient, say so in "open_questions".

Package: {dist}
Declared: {declared}
PyPI metadata: {meta}
Observed risk indicators:
{indicators}
Usage locations in the repository (file:line  code):
{usages}

Respond with ONLY a JSON object, no prose, matching exactly:
{{
  "action": "upgrade" | "replace" | "remove" | "declare" | "keep",
  "replacement": "package name and version spec to move to (e.g. 'platformdirs>=4' or 'pydantic>=2,<3')",
  "rationale": "2-3 sentences tied to the evidence",
  "migration_steps": ["ordered, concrete steps"],
  "code_changes": [{{"file": "path", "line": 0, "before": "exact current code", "after": "proposed code"}}],
  "compatibility_warnings": ["behaviour differences or breaking changes to watch"],
  "suggested_tests": ["specific tests to add or run"],
  "open_questions": ["what could not be determined from the evidence"],
  "confidence": "low" | "medium" | "high"
}}"""


def recommend(package: dict, meta: dict | None, indicators: list[dict]) -> dict:
    """Ask Gemma 4 for a replacement plan for one package. Always returns a dict."""
    usages = "\n".join(f"{u['file']}:{u['line']}  {u['code']}" for u in package["usages"][:20]) or "(no usages found)"
    declared = "; ".join(f"{d['source_file']}:{d['line']} {d['name']}{d['specifier']} ({d['scope']})"
                         for d in package["declared_in"]) or "not declared"
    meta_txt = {k: meta.get(k) for k in ("latest_version", "latest_release_date", "pinned_version",
                                         "pinned_release_date", "pinned_requires_python")} if meta else "unavailable"
    prompt = PROMPT.format(dist=package["distribution"], declared=declared, meta=json.dumps(meta_txt),
                           indicators="\n".join(f"- [{i['severity']}] {i['message']}" for i in indicators),
                           usages=usages)
    try:
        model = model_name()
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.2, "maxOutputTokens": 2048}}
        resp = _request(f"{API}/{model}:generateContent", body)
        parts = resp.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        return {"ok": True, "model": model.removeprefix("models/"), "recommendation": _extract_json(text)}
    except (GemmaError, json.JSONDecodeError, KeyError, IndexError) as exc:
        return {"ok": False, "error": str(exc)}
