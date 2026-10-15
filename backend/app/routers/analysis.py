"""API endpoints used by the web frontend (and judges via /docs)."""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

from app.services.capsule import analyse, build_capsule

router = APIRouter(prefix="/api", tags=["analysis"])
_latest: dict = {}

DEFAULT_REPO = "https://github.com/rishrek/LinkWatch"


class CapsuleRequest(BaseModel):
    repo: str = DEFAULT_REPO
    use_gemma: bool = True
    run_tests: bool = False


@router.get("/scan")
def scan(repo: str = Query(DEFAULT_REPO, description="GitHub URL or local folder path")):
    """Fast scan: dependencies, usages, observed risk. No AI, no test execution."""
    try:
        result, _ = analyse(repo)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result


@router.post("/capsule")
def capsule(body: CapsuleRequest):
    """Full pipeline: scan, risk, Gemma 4 recommendations, optional test runs, Exit Capsule."""
    try:
        result = build_capsule(body.repo, use_gemma=body.use_gemma, run_test_suite=body.run_tests)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _latest["capsule"] = result
    return result


@router.get("/capsule/latest.md", response_class=PlainTextResponse)
def latest_markdown():
    if "capsule" not in _latest:
        raise HTTPException(status_code=404, detail="No capsule generated yet")
    return PlainTextResponse(_latest["capsule"]["markdown"], headers={
        "Content-Disposition": 'attachment; filename="dependency-exit-capsule.md"'})


@router.get("/capsule/latest.json")
def latest_json():
    if "capsule" not in _latest:
        raise HTTPException(status_code=404, detail="No capsule generated yet")
    return JSONResponse({k: v for k, v in _latest["capsule"].items() if k != "markdown"}, headers={
        "Content-Disposition": 'attachment; filename="dependency-exit-capsule.json"'})
