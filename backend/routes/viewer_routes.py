# backend/routes/viewer_routes.py
"""Read-only viewer endpoints to avoid black-box demos.

Expose mocked source data (Jira/Xray/Bitbucket) and the effective prompt.
"""
from __future__ import annotations

from fastapi import APIRouter

from backend.data_client.jira_client import get_jira_issue
from backend.data_client.xray_client import get_xray_tests_for_issue
from backend.data_client.bitbucket_client import get_bitbucket_changes_for_issue
from backend.llm_client.llm_agent import SYSTEM_PROMPT, _build_prompt

router = APIRouter(tags=["viewer"])


@router.get("/api/jira/issue/{jira_key}")
def viewer_jira_issue(jira_key: str):
    issue = get_jira_issue(jira_key)  # fallback-friendly si absent
    return {"data": issue.model_dump(), "meta": {"jira_key": jira_key}, "errors": []}


@router.get("/api/xray/tests/{jira_key}")
def viewer_xray_tests(jira_key: str):
    tests = get_xray_tests_for_issue(jira_key)
    return {
        "data": [t.model_dump() for t in tests],
        "meta": {"jira_key": jira_key, "count": len(tests)},
        "errors": [],
    }


@router.get("/api/bitbucket/changes/{jira_key}")
def viewer_bitbucket_changes(jira_key: str):
    changes = get_bitbucket_changes_for_issue(jira_key)
    return {
        "data": [c.model_dump() for c in changes],
        "meta": {"jira_key": jira_key, "count": len(changes)},
        "errors": [],
    }


@router.get("/api/llm/prompt/{jira_key}")
def viewer_llm_prompt(jira_key: str):
    issue = get_jira_issue(jira_key)
    tests = get_xray_tests_for_issue(jira_key)
    changes = get_bitbucket_changes_for_issue(jira_key)

    user_prompt = _build_prompt(issue, tests, changes)

    return {
        "data": {
            "jira_key": jira_key,
            "system_prompt": SYSTEM_PROMPT,
            "user_prompt": user_prompt,
        },
        "meta": {"jira_key": jira_key},
        "errors": [],
    }
