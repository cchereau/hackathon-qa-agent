# backend/routes/diag_routes.py
import logging
from pathlib import Path

from fastapi import APIRouter

logger = logging.getLogger("qa-test-plan-agent")
router = APIRouter(tags=["diag"])

# Paths/utils diagnostics (fixes + visibility)
try:
    from backend.utils import (
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
    REPO_ROOT = PROJECT_ROOT = MOCK_ROOT = None
    JIRA_MOCK_DIR = XRAY_MOCK_DIR = BITBUCKET_MOCK_DIR = None
    JIRA_ISSUES_FILE = XRAY_TESTS_FILE = XRAY_PLANS_FILE = BITBUCKET_CHANGES_FILE = None


@router.get("/health")
def health():
    return {"status": "ok", "mode": "full-mock"}


@router.get("/llm/health")
async def llm_health():
    return {"status": "ok"}


@router.get("/api/diag/paths")
def diag_paths():
    def _p(x):
        return str(x) if x is not None else None

    def _exists(x):
        try:
            return bool(x and Path(x).exists())
        except Exception:
            return False

    return {
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


from backend.config import config_diag_safe


@router.get("/api/diag/config")
def diag_config():
    return config_diag_safe()


from backend.llm_client.llm_client import LLMClient


@router.get("/api/diag/llm")
async def diag_llm():
    llm = LLMClient()
    content = await llm.chat(
        [
            {"role": "system", "content": "You are a diagnostic bot."},
            {"role": "user", "content": "Reply with: OK"},
        ]
    )
    return {"ok": True, "provider": getattr(llm, "provider", None), "reply": content[:200]}
