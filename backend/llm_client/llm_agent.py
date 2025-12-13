# backend/llm_client/llm_agent.py
import json
import logging
from typing import List, Optional, Tuple

from .llm_client import LLMClient
from .models import (
    JiraIssue,
    XrayTest,
    CodeChange,
    TestPlanResponse,
    TestCaseSuggestion,
)

logger = logging.getLogger("qa-test-plan-agent.llm_agent")

_llm: Optional[LLMClient] = None


def _get_llm() -> LLMClient:
    global _llm
    if _llm is None:
        _llm = LLMClient()
    return _llm


# ---------------------------------------------------------------------
# Prompting
# ---------------------------------------------------------------------
SYSTEM_PROMPT = """
You are a senior QA engineer.

Your task is to generate TWO outputs:

1) A detailed test plan in Markdown.
2) A list of NEW test case suggestions in JSON.

IMPORTANT RULES:
- Output MUST follow the exact format below.
- The JSON MUST be valid and parsable.
- The JSON MUST match this schema:

[
  {
    "title": string,
    "priority": "HIGH" | "MEDIUM" | "LOW",
    "type": "functional" | "regression" | "security" | "performance",
    "given": string,
    "when": string,
    "then": string,
    "mapped_existing_test_key": string | null
  }
]

FORMAT (STRICT):

---MARKDOWN---
<markdown content>

---SUGGESTIONS_JSON---
<json array>
""".strip()


def _build_prompt(
    issue: JiraIssue,
    tests: List[XrayTest],
    changes: List[CodeChange],
) -> str:
    return f"""
JIRA ISSUE
----------
Key: {issue.key}
Summary: {issue.summary}
Description:
{issue.description}

Acceptance Criteria:
{issue.acceptance_criteria}

EXISTING XRAY TESTS
------------------
{[f"{t.key}: {t.summary}" for t in tests]}

CODE CHANGES
------------
{[c.file_path for c in changes]}

Instructions:
- Reuse existing tests where relevant.
- Propose NEW test cases only in the JSON suggestions.
- Be precise and actionable.
""".strip()


# ---------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------
def _split_llm_output(content: str) -> Tuple[str, List[TestCaseSuggestion]]:
    """
    Extract markdown + suggestions JSON from the LLM response.
    """
    md_marker = "---MARKDOWN---"
    json_marker = "---SUGGESTIONS_JSON---"

    if md_marker not in content or json_marker not in content:
        logger.warning("LLM output does not respect expected format")
        return content, []

    markdown = content.split(md_marker, 1)[1].split(json_marker, 1)[0].strip()
    json_part = content.split(json_marker, 1)[1].strip()

    suggestions: List[TestCaseSuggestion] = []
    try:
        raw_items = json.loads(json_part)
        if isinstance(raw_items, list):
            for item in raw_items:
                suggestions.append(TestCaseSuggestion(**item))
    except Exception as exc:
        logger.error(f"Failed to parse suggestions JSON: {exc}")

    return markdown, suggestions


# ---------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------
async def generate_test_plan(
    issue: JiraIssue,
    tests: List[XrayTest],
    changes: List[CodeChange],
) -> TestPlanResponse:
    llm = _get_llm()

    prompt = _build_prompt(issue, tests, changes)

    content = await llm.chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
    )

    markdown, suggestions = _split_llm_output(content)

    return TestPlanResponse(
        jira_key=issue.key,
        markdown=markdown,
        suggestions=suggestions,
        raw_context={
            "issue": issue.model_dump(),
            "tests": [t.model_dump() for t in tests],
            "changes": [c.model_dump() for c in changes],
        },
    )
