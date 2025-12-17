# backend/data_client/xray_client.py
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

Hackathon notes:
- Jira keys are expected to be "US-xxx" (e.g., US-401).
- Some legacy mock datasets may still contain "PROJ-xxx".
  This client provides a safe fallback lookup (US <-> PROJ) to keep T0 robust.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.llm_client.models import XrayTest
from backend.utils import (
    XRAY_TESTS_FILE,
    XRAY_PLANS_FILE,
    load_json_file,
    save_json_file,
    xray_plans_overlay_file,
)

# ----------------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------------
def _normalize_key_candidates(jira_key: str) -> List[str]:
    """
    Return candidate keys to lookup in tests_by_requirement.json.

    Primary key is used first. Then we try a US <-> PROJ fallback mapping
    to support legacy datasets without breaking T0.
    """
    jk = (jira_key or "").strip()
    if not jk:
        return []

    out = [jk]

    # Legacy support: some mocks used "PROJ-401" while new ones use "US-401"
    if jk.startswith("US-"):
        out.append("PROJ-" + jk[3:])
    elif jk.startswith("PROJ-"):
        out.append("US-" + jk[5:])

    # Dedup while preserving order
    seen = set()
    uniq: List[str] = []
    for k in out:
        if k not in seen:
            seen.add(k)
            uniq.append(k)
    return uniq


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
        "US-401":   [ {XrayTest}, {XrayTest}, ... ],
        "US-402":   [ ... ],
        "PROJ-401": [ ... ]   # optional legacy
      }

    Notes:
    - `load_json_file()` returns Any (dict or list), so we must type-guard.
    - We support US <-> PROJ fallback lookup to keep T0 resilient.
    """
    raw = load_json_file(XRAY_TESTS_FILE)
    data: Dict[str, Any] = raw if isinstance(raw, dict) else {}

    raw_tests: Any = None
    for key in _normalize_key_candidates(jira_key):
        if key in data:
            raw_tests = data.get(key)
            break

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
          "key": "TP-001",
          "summary": "...",
          "jira_keys": ["US-401", "US-402"],
          "tests": ["TEST-US-401-1", ...]
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
    overlay_name = (overlay_name or "").strip()
    if not overlay_name:
        return []

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

    Safety:
    - Ensure parent directory exists at write time (no side effects at import time).
    """
    overlay_name = (overlay_name or "").strip()
    if not overlay_name:
        # no-op (avoid writing "test_plans_enriched..json")
        return

    path = xray_plans_overlay_file(overlay_name)

    # Ensure directory exists (only here, on save)
    parent: Path = path.parent
    parent.mkdir(parents=True, exist_ok=True)

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

    overlay_name = (overlay_name or "").strip()
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
