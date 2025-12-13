# backup/xray_client.py
"""
Mock Xray client – reads tests and pre‑built plans from JSON files.
"""

from typing import List, Any
from backend.llm_client.models import XrayTest
from backend.utils import (
    XRAY_TESTS_FILE,
    XRAY_PLANS_FILE,
    load_json_file,
)


def _load_tests_from_file(jira_key: str) -> List[XrayTest]:
    data: dict[str, Any] = load_json_file(XRAY_TESTS_FILE)
    raw_tests = data.get(jira_key, [])
    return [XrayTest(**t) for t in raw_tests]


def get_xray_tests_for_issue(jira_key: str) -> List[XrayTest]:
    """Public API used by `main.py`."""
    return _load_tests_from_file(jira_key)


def get_prebuilt_test_plan(jira_key: str) -> dict | None:
    """Return a pre‑generated test plan if it exists."""
    data = load_json_file(XRAY_PLANS_FILE)
    return data.get(jira_key)