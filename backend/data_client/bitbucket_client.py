# backup/bitbucket_client.py
"""
Mock Bitbucket client – reads code‑change data from JSON.
"""

from typing import List, Any
from backend.llm_client.models import CodeChange
from backend.utils import BITBUCKET_CHANGES_FILE, load_json_file


def _load_changes_from_file(jira_key: str) -> List[CodeChange]:
    data: dict[str, Any] = load_json_file(BITBUCKET_CHANGES_FILE)
    raw = data.get(jira_key, [])
    return [CodeChange(**c) for c in raw]


def get_bitbucket_changes_for_issue(jira_key: str) -> List[CodeChange]:
    """Public API used by `main.py`."""
    return _load_changes_from_file(jira_key)