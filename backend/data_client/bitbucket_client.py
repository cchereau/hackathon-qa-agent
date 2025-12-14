# backend/data_client/bitbucket_client.py
"""Mock Bitbucket client (T0).

Reads code-change data from JSON.

T0 requirements:
- Never raise a 500 because of incomplete mock data.
- Be tolerant to missing fields so the UI remains stable.

Source file:
  mocks/bitbucket/changes_by_jira_key.json

Expected format:
  {
    "US-402": [
      {"file_path": "src/...", "summary": "...", "diff_excerpt": "..."},
      ...
    ],
    "US-403": [...]
  }

If a change entry is missing required fields, we:
- keep the record (best effort) with safe defaults,
- or skip the record if it is not meaningful at all.
"""

from __future__ import annotations

from typing import Any, Dict, List

from backend.llm_client.models import CodeChange
from backend.utils import BITBUCKET_CHANGES_FILE, load_json_file


def _normalize_change(raw: Dict[str, Any], idx: int) -> Dict[str, Any]:
    """Best-effort normalization for a single change item."""

    file_path = (raw.get("file_path") or raw.get("path") or raw.get("file") or "").strip() if isinstance(raw, dict) else ""
    summary = (raw.get("summary") if isinstance(raw, dict) else None) or None
    diff_excerpt = (raw.get("diff_excerpt") if isinstance(raw, dict) else None) or None

    # If we have *no* file path, we still keep the record but mark it clearly.
    if not file_path:
        file_path = f"UNKNOWN_FILE_{idx:03d}"

    # Ensure strings (Pydantic will accept None for optional fields)
    if summary is not None and not isinstance(summary, str):
        summary = str(summary)
    if diff_excerpt is not None and not isinstance(diff_excerpt, str):
        diff_excerpt = str(diff_excerpt)

    return {"file_path": file_path, "summary": summary, "diff_excerpt": diff_excerpt}


def _load_changes_from_file(jira_key: str) -> List[CodeChange]:
    data: Dict[str, Any] = load_json_file(BITBUCKET_CHANGES_FILE)
    raw = data.get(jira_key, []) if isinstance(data, dict) else []
    if not isinstance(raw, list):
        raw = []

    out: List[CodeChange] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        try:
            normalized = _normalize_change(item, idx)
            out.append(CodeChange(**normalized))
        except Exception:
            # Must not crash T0 because a mock item is slightly malformed.
            continue
    return out


def get_bitbucket_changes_for_issue(jira_key: str) -> List[CodeChange]:
    """Public API."""
    return _load_changes_from_file(jira_key)
