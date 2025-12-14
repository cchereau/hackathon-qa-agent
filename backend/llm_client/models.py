from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any


# ─────────────────────────────────────────────────────────────
# Requests
# ─────────────────────────────────────────────────────────────

class TestPlanRequest(BaseModel):
    """
    Request used by /agent/test-plan
    """
    jira_key: str = Field(..., description="Clé Jira au format US-XXX (ex: US-402)")


# ─────────────────────────────────────────────────────────────
# Core domain models
# ─────────────────────────────────────────────────────────────

class JiraIssue(BaseModel):
    key: str
    summary: str
    description: str
    acceptance_criteria: Optional[str] = None


class XrayTest(BaseModel):
    """
    Existing test case coming from Xray (mocked).
    """
    key: str
    summary: str
    steps: Optional[str] = None

    # Extension non cassante :
    # - utilisée par G4 pour gouvernance / couverture / pertinence
    # - totalement optionnelle (anciens fichiers OK)
    tags: Optional[List[str]] = None


class CodeChange(BaseModel):
    """
    Code change extracted from Bitbucket (mocked).
    """
    file_path: str
    summary: Optional[str] = None
    diff_excerpt: Optional[str] = None


# ─────────────────────────────────────────────────────────────
# LLM output models
# ─────────────────────────────────────────────────────────────

class TestCaseSuggestion(BaseModel):
    """
    New test case suggested by the LLM (not yet existing in Xray).
    """
    title: str
    priority: str               # HIGH / MEDIUM / LOW
    type: str                   # functional / security / performance / regression
    given: str
    when: str
    then: str

    # Optional mapping to an existing test (reuse / refactor scenario)
    mapped_existing_test_key: Optional[str] = None


class TestPlanResponse(BaseModel):
    """
    Response returned by the LLM agent for a single Jira issue.
    Used by /agent/test-plan.
    """
    jira_key: str
    markdown: str               # Human-readable test plan
    suggestions: List[TestCaseSuggestion]

    # Full raw context passed to / used by the LLM
    # (Jira issue, tests, code changes, metrics, etc.)
    raw_context: Dict[str, Any]
