# backend/routes/agent_routes.py
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.data_client.jira_client import get_jira_issue
from backend.data_client.xray_client import get_xray_tests_for_issue
from backend.data_client.bitbucket_client import get_bitbucket_changes_for_issue
from backend.llm_client.llm_agent import generate_test_plan
from backend.errors import LLMConnectionError

logger = logging.getLogger("qa-test-plan-agent")

router = APIRouter(tags=["agent"])


class TestPlanRequest(BaseModel):
    jira_key: str = Field(..., description="Clé Jira, ex: PROJ-401")


@router.post("/agent/test-plan")
async def create_test_plan(req: TestPlanRequest):
    """
    Generate a test plan for a single Jira issue via the LLM.
    """
    try:
        issue = get_jira_issue(req.jira_key)
    except Exception as exc:
        logger.error(f"[Jira] Failed to fetch issue {req.jira_key}: {exc}")
        raise HTTPException(
            status_code=502,
            detail={"source": "jira", "message": f"Impossible de récupérer l'issue Jira '{req.jira_key}'.", "reason": str(exc)},
        )

    try:
        tests = get_xray_tests_for_issue(req.jira_key)
    except Exception as exc:
        logger.error(f"[Xray] Failed to fetch tests: {exc}")
        raise HTTPException(
            status_code=502,
            detail={"source": "xray", "message": f"Impossible de récupérer les tests Xray pour l'issue '{req.jira_key}'.", "reason": str(exc)},
        )

    try:
        changes = get_bitbucket_changes_for_issue(req.jira_key)
    except Exception as exc:
        logger.error(f"[Bitbucket] Failed to fetch changes: {exc}")
        raise HTTPException(
            status_code=502,
            detail={"source": "bitbucket", "message": f"Impossible de récupérer les changements Bitbucket pour l'issue '{req.jira_key}'.", "reason": str(exc)},
        )

    try:
        plan = await generate_test_plan(issue, tests, changes)
    except RuntimeError as exc:
        logger.error(f"[LLM] Generation failed: {exc}")
        raise LLMConnectionError(str(exc))
    except Exception as exc:
        logger.error(f"[LLM] Unexpected error: {exc}")
        raise HTTPException(
            status_code=502,
            detail={"source": "llm_agent", "message": "Le moteur de génération de plan de test a échoué.", "reason": str(exc)},
        )

    return plan
