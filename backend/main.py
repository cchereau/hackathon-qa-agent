# backend/main.py
import logging
import traceback
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ----------------------------------------------------------------------
# Import des clients / agents (local to this package)
# ----------------------------------------------------------------------
from backend.data_client.jira_client import get_jira_issue
from backend.data_client.xray_client import (
    get_xray_tests_for_issue,
    get_prebuilt_test_plan,
)
from backend.data_client.bitbucket_client import get_bitbucket_changes_for_issue
from backend.llm_client.llm_agent import generate_test_plan

# ----------------------------------------------------------------------
# Paths/utils diagnostics (fixes + visibility)
# ----------------------------------------------------------------------
# NOTE: your utils are in backup/utils.py in the snippet you provided.
# If you actually import from backend.utils, update accordingly.
try:
    from .utils import (
        REPO_ROOT,
        PROJECT_ROOT,
        MOCK_ROOT,
        JIRA_MOCK_DIR,
        XRAY_MOCK_DIR,
        BITBUCKET_MOCK_DIR,
        JIRA_ISSUES_FILE,
        XRAY_TESTS_FILE,
        XRAY_PLANS_FILE,
        BITBUCKET_CHANGES_FILE,
    )
except Exception:
    # Fallback: avoid crashing if utils location differs in your current branch
    REPO_ROOT = PROJECT_ROOT = MOCK_ROOT = None
    JIRA_MOCK_DIR = XRAY_MOCK_DIR = BITBUCKET_MOCK_DIR = None
    JIRA_ISSUES_FILE = XRAY_TESTS_FILE = XRAY_PLANS_FILE = BITBUCKET_CHANGES_FILE = None

# ----------------------------------------------------------------------
# Metrics endpoint (Prometheus)
# ----------------------------------------------------------------------
from backend.metrics import LLM_REQUESTS, LLM_LATENCY  # imported for side-effects only
from prometheus_client import make_asgi_app
from backend.metrics import REGISTRY
# ----------------------------------------------------------------------
# Logger configuration
# ----------------------------------------------------------------------
logger = logging.getLogger("qa-test-plan-agent")
logger.setLevel(logging.INFO)  # switch to DEBUG for dev
handler = logging.StreamHandler()
formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
handler.setFormatter(formatter)

# Avoid duplicated handlers on reload (uvicorn --reload)
if not logger.handlers:
    logger.addHandler(handler)
else:
    # Replace formatting on existing handlers if needed
    for h in logger.handlers:
        h.setFormatter(formatter)

# ----------------------------------------------------------------------
# FastAPI app + CORS
# ----------------------------------------------------------------------
app = FastAPI()


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------------------------------------------------
# Mount Prometheus metrics at /metrics
# ----------------------------------------------------------------------
from backend.routes.jira_project_routes import router as jira_project_router
app.include_router(jira_project_router)

app.mount("/metrics", make_asgi_app(registry=REGISTRY))

# ----------------------------------------------------------------------
# Request model
# ----------------------------------------------------------------------
class TestPlanRequest(BaseModel):
    jira_key: str

# ----------------------------------------------------------------------
# Simple health-check
# ----------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "mode": "full-mock"}

# ----------------------------------------------------------------------
# LLM health-check – lightweight call to the LLM endpoint
# (For now: returns ok. Later: call the provider ping endpoint.)
# ----------------------------------------------------------------------
@app.get("/llm/health")
async def llm_health():
    return {"status": "ok"}

# ----------------------------------------------------------------------
# Diagnostic endpoint: validates resolved paths & existence (critical for hackathon)
# ----------------------------------------------------------------------
@app.get("/api/diag/paths")
def diag_paths():
    def _p(x):
        return str(x) if x is not None else None

    def _exists(x):
        try:
            return bool(x and Path(x).exists())
        except Exception:
            return False

    payload = {
        "cwd": str(Path.cwd()),
        "repo_root": _p(REPO_ROOT),
        "project_root": _p(PROJECT_ROOT),
        "mock_root": _p(MOCK_ROOT),
        "jira_mock_dir": _p(JIRA_MOCK_DIR),
        "xray_mock_dir": _p(XRAY_MOCK_DIR),
        "bitbucket_mock_dir": _p(BITBUCKET_MOCK_DIR),
        "jira_issues_file": _p(JIRA_ISSUES_FILE),
        "xray_tests_file": _p(XRAY_TESTS_FILE),
        "xray_plans_file": _p(XRAY_PLANS_FILE),
        "bitbucket_changes_file": _p(BITBUCKET_CHANGES_FILE),
        "exists": {
            "mock_root": _exists(MOCK_ROOT),
            "jira_issues_file": _exists(JIRA_ISSUES_FILE),
            "xray_tests_file": _exists(XRAY_TESTS_FILE),
            "xray_plans_file": _exists(XRAY_PLANS_FILE),
            "bitbucket_changes_file": _exists(BITBUCKET_CHANGES_FILE),
        },
    }
    return payload

from backend.config import config_diag_safe
@app.get("/api/diag/config")
def diag_config():
    return config_diag_safe()
from backend.llm_client.llm_client import LLMClient

@app.get("/api/diag/llm")
async def diag_llm():
    llm = LLMClient()
    content = await llm.chat([
        {"role": "system", "content": "You are a diagnostic bot."},
        {"role": "user", "content": "Reply with: OK"}
    ])
    return {"ok": True, "provider": getattr(llm, "provider", None), "reply": content[:200]}




# ----------------------------------------------------------------------
# Generic exception handler (fallback)
# ----------------------------------------------------------------------
@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error(
        f"Unhandled exception on {request.url.path}: {exc}\n{traceback.format_exc()}"
    )
    detail = str(exc) if app.debug else "Internal server error"
    return JSONResponse(
        status_code=500,
        content={"error": "internal_server_error", "detail": detail},
    )

# ----------------------------------------------------------------------
# Custom exception for LLM-related failures (optional but handy)
# ----------------------------------------------------------------------
class LLMConnectionError(RuntimeError):
    """Raised when the LLM service cannot be reached or returns an error."""

    pass

@app.exception_handler(LLMConnectionError)
async def llm_error_handler(request: Request, exc: LLMConnectionError):
    logger.error(f"LLM error on {request.url.path}: {exc}")
    return JSONResponse(
        status_code=502,
        content={"error": "llm_unavailable", "detail": str(exc)},
    )

# ----------------------------------------------------------------------
# Main endpoint – orchestrates the whole workflow
# ----------------------------------------------------------------------
@app.post("/agent/test-plan")
async def create_test_plan(req: TestPlanRequest):
    # 0) Pre-built plan (fast-path)
    try:
        prebuilt = get_prebuilt_test_plan(req.jira_key)
        if prebuilt is not None:
            return prebuilt
    except Exception as exc:
        logger.warning(f"[Xray] Could not load pre-built test plan: {exc}")

    # 1) Fetch Jira issue
    try:
        issue = get_jira_issue(req.jira_key)
    except Exception as exc:
        logger.error(f"[Jira] Failed to fetch issue {req.jira_key}: {exc}")
        raise HTTPException(
            status_code=502,
            detail={
                "source": "jira",
                "message": f"Impossible de récupérer l'issue Jira '{req.jira_key}'.",
                "reason": str(exc),
            },
        )

    # 2) Fetch Xray tests
    try:
        tests = get_xray_tests_for_issue(req.jira_key)
    except Exception as exc:
        logger.error(f"[Xray] Failed to fetch tests: {exc}")
        raise HTTPException(
            status_code=502,
            detail={
                "source": "xray",
                "message": f"Impossible de récupérer les tests Xray pour l'issue '{req.jira_key}'.",
                "reason": str(exc),
            },
        )

    # 3) Fetch Bitbucket changes
    try:
        changes = get_bitbucket_changes_for_issue(req.jira_key)
    except Exception as exc:
        logger.error(f"[Bitbucket] Failed to fetch changes: {exc}")
        raise HTTPException(
            status_code=502,
            detail={
                "source": "bitbucket",
                "message": f"Impossible de récupérer les changements Bitbucket pour l'issue '{req.jira_key}'.",
                "reason": str(exc),
            },
        )

    # 4) Generate test plan via LLM
    try:
        plan = await generate_test_plan(issue, tests, changes)
    except RuntimeError as exc:
        logger.error(f"[LLM] Generation failed: {exc}")
        raise LLMConnectionError(str(exc))
    except Exception as exc:
        logger.error(f"[LLM] Unexpected error: {exc}")
        raise HTTPException(
            status_code=502,
            detail={
                "source": "llm_agent",
                "message": "Le moteur de génération de plan de test a échoué.",
                "reason": str(exc),
            },
        )

    return plan
