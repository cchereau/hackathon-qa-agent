# backup/xray_client.py
"""
Mock Xray client.

Responsibilities:
- Read existing Xray tests from tests_by_requirement.json (by Jira key)
- Read baseline test plans from test_plans.json (catalog of campaigns)
- Read / write enriched test plan overlays stored next to test_plans.json

IMPORTANT:
- test_plans.json is a LIST of plans (catalog), NOT a dict by Jira key
- Overlays are stored as:
    test_plans_enriched.<overlay_name>.json
"""

from __future__ import annotations

from typing import List, Any, Dict, Optional

from backend.llm_client.models import XrayTest
from backend.utils import (
    XRAY_TESTS_FILE,
    XRAY_PLANS_FILE,
    load_json_file,
    save_json_file,
    xray_plans_overlay_file,
)

# ----------------------------------------------------------------------
# Xray tests (by Jira issue)
# ----------------------------------------------------------------------
def _load_tests_from_file(jira_key: str) -> List[XrayTest]:
    """
    Load existing Xray tests linked to a Jira issue.

    Source file:
      mocks/xray/tests_by_requirement.json

    Expected format:
      {
        "PROJ-401": [ {XrayTest}, {XrayTest}, ... ],
        "PROJ-402": [ ... ]
      }

    Note:
    - `load_json_file()` returns Any (dict or list), so we must type-guard.
    """
    raw = load_json_file(XRAY_TESTS_FILE)
    data: Dict[str, Any] = raw if isinstance(raw, dict) else {}

    raw_tests = data.get(jira_key, [])
    if not isinstance(raw_tests, list):
        raw_tests = []

    # tags are optional and automatically supported by the model
    return [XrayTest(**t) for t in raw_tests if isinstance(t, dict)]


def get_xray_tests_for_issue(jira_key: str) -> List[XrayTest]:
    """
    Public API: return existing Xray tests for a Jira issue.
    """
    return _load_tests_from_file(jira_key)


# ----------------------------------------------------------------------
# Test plans – baseline catalog
# ----------------------------------------------------------------------
def list_test_plans() -> List[dict]:
    """
    Return baseline test plans catalog.

    Source file:
      mocks/xray/test_plans.json

    Expected format:
      [
        {
          "key": "TP-REG-AUTH",
          "summary": "...",
          "jira_keys": [...],
          "tests": [...]
        },
        ...
      ]
    """
    raw = load_json_file(XRAY_PLANS_FILE)
    if not isinstance(raw, list):
        # Strict but safe: baseline plans must be a list
        return []
    return [p for p in raw if isinstance(p, dict)]


def get_test_plan(plan_key: str) -> Optional[dict]:
    """
    Return a single baseline test plan by its plan key.
    """
    plan_key = (plan_key or "").strip()
    if not plan_key:
        return None

    for plan in list_test_plans():
        if (plan.get("key") or "").strip() == plan_key:
            return plan
    return None


# ----------------------------------------------------------------------
# Test plans overlays (enriched by G4)
# ----------------------------------------------------------------------
def load_test_plans_overlay(overlay_name: str) -> List[dict]:
    """
    Load an overlay file if it exists.

    File:
      mocks/xray/test_plans_enriched.<overlay_name>.json

    Returns:
      - list of plan objects (same keys as baseline, plus overlay/governance)
      - empty list if file does not exist
    """
    path = xray_plans_overlay_file(overlay_name)
    if not path.exists():
        return []

    raw = load_json_file(path)
    if not isinstance(raw, list):
        return []

    return [p for p in raw if isinstance(p, dict)]


def save_test_plans_overlay(overlay_name: str, plans: List[dict]) -> None:
    """
    Persist an overlay file next to test_plans.json.
    """
    path = xray_plans_overlay_file(overlay_name)
    save_json_file(path, plans)


def get_test_plan_with_overlay(
    plan_key: str,
    overlay_name: Optional[str] = None,
) -> Optional[dict]:
    """
    Return a test plan merged with its overlay (if provided).

    Merge rules:
    - baseline plan is the base
    - overlay wins for:
        - governance
        - overlay
        - (optionally) summary / jira_keys / tests if present
    """
    base = get_test_plan(plan_key)
    if base is None:
        return None

    if not overlay_name:
        return base

    overlay_plans = load_test_plans_overlay(overlay_name)
    overlay_plan = None
    for p in overlay_plans:
        if (p.get("key") or "").strip() == (plan_key or "").strip():
            overlay_plan = p
            break

    if not overlay_plan:
        return base

    merged = dict(base)

    # Overlay sections
    for k in ("governance", "overlay"):
        if k in overlay_plan:
            merged[k] = overlay_plan.get(k)

    # Allow overlay to override structural fields if explicitly provided
    for k in ("summary", "jira_keys", "tests"):
        if k in overlay_plan:
            merged[k] = overlay_plan.get(k)

    return merged


# ----------------------------------------------------------------------
# Deprecated API (kept for clarity)
# ----------------------------------------------------------------------
def get_prebuilt_test_plan(jira_key: str) -> None:
    """
    DEPRECATED / INTENTIONALLY DISABLED.

    Old behavior assumed XRAY_PLANS_FILE was a dict indexed by Jira key.
    This is NOT compatible with the current format (list of plans).

    Do NOT use.
    """
    return None
