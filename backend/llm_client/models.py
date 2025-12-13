from pydantic import BaseModel, Field
from typing import List, Optional


class TestPlanRequest(BaseModel):
    jira_key: str = Field(..., description="Clé Jira, ex: PROJ-123")


class JiraIssue(BaseModel):
    key: str
    summary: str
    description: str
    acceptance_criteria: Optional[str] = None


class XrayTest(BaseModel):
    key: str
    summary: str
    steps: Optional[str] = None


class CodeChange(BaseModel):
    file_path: str
    summary: Optional[str] = None
    diff_excerpt: Optional[str] = None


class TestCaseSuggestion(BaseModel):
    title: str
    priority: str
    type: str
    given: str
    when: str
    then: str
    mapped_existing_test_key: Optional[str] = None


class TestPlanResponse(BaseModel):
    jira_key: str
    markdown: str
    suggestions: List[TestCaseSuggestion]
    raw_context: dict

