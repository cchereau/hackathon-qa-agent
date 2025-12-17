# backend/routes/test_plans_effective_routes.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query

from backend.data_client.xray_client import get_test_plan, get_test_plan_with_overlay
from backend.routes.test_plans_routes import (
    _overlay_status,
    _is_run_overlay_name,
    _load_run_doc,
    _compute_run_overlay_for_plan,
    _merge_overlay_into_plan,
)

router = APIRouter(prefix="/api/test-plans", tags=["test-plans-effective"])


# ─────────────────────────────────────────────────────────────
# Normalization helpers (local, pure)
# ─────────────────────────────────────────────────────────────
def _as_dict(x: Any) -> Dict[str, Any]:
    return x if isinstance(x, dict) else {}


def _as_list(x: Any) -> List[Any]:
    return x if isinstance(x, list) else []


def _as_list_str(x: Any) -> List[str]:
    return [i for i in _as_list(x) if isinstance(i, str)]


def _extract_candidate_keys_from_run_overlay(plan: Dict[str, Any]) -> List[str]:
    """
    Run overlay shape (computed in-memory in test_plans_routes):
      plan["overlay"]["candidate_tests"] = [{candidate_key, ...}, ...]
    """
    ov = _as_dict(plan.get("overlay"))
    cand = _as_list(ov.get("candidate_tests"))
    keys: List[str] = []
    for c in cand:
        if isinstance(c, dict):
            ck = c.get("candidate_key")
            if isinstance(ck, str) and ck:
                keys.append(ck)
    return keys


def _extract_ai_decisions_from_file_overlay(plan: Dict[str, Any]) -> Tuple[List[str], List[str], List[str]]:
    """
    File overlay shape (persisted):
      plan["overlay"]["ai_candidates"] = [{candidate_key, decision, ...}, ...]
    """
    ov = _as_dict(plan.get("overlay"))
    ai = _as_list(ov.get("ai_candidates"))

    accepted: List[str] = []
    rejected: List[str] = []
    pending: List[str] = []

    for c in ai:
        if not isinstance(c, dict):
            continue
        ck = c.get("candidate_key")
        if not isinstance(ck, str) or not ck:
            continue

        decision = c.get("decision")
        dec = decision.upper() if isinstance(decision, str) else "PENDING"

        if dec == "ACCEPTED":
            accepted.append(ck)
        elif dec == "REJECTED":
            rejected.append(ck)
        else:
            pending.append(ck)

    return accepted, rejected, pending


def _extract_skip_test_keys(overlay_block: Dict[str, Any]) -> List[str]:
    """
    Accepts multiple shapes for existing_tests_to_skip:
      - ["T-1", "T-2", ...]
      - [{"test_key": "T-1"}, {"key": "T-2"}, ...]
      - mixed list
    Returns: list of unique, non-empty string keys (order preserved).
    """
    raw = _as_list(overlay_block.get("existing_tests_to_skip"))
    out: List[str] = []
    seen = set()

    for item in raw:
        key: Optional[str] = None

        if isinstance(item, str):
            key = item
        elif isinstance(item, dict):
            # common shapes used in UI/backend
            v1 = item.get("test_key")
            v2 = item.get("key")
            v3 = item.get("test")  # just in case
            for v in (v1, v2, v3):
                if isinstance(v, str) and v.strip():
                    key = v
                    break

        if key:
            k = key.strip()
            if k and k not in seen:
                seen.add(k)
                out.append(k)

    return out


@router.get("/{plan_key}/effective")
def api_effective_test_plan(
    plan_key: str,
    overlay: Optional[str] = Query(default=None),
):
    """
    Compute the EFFECTIVE test plan (read-only view).

    Effective = what you would actually execute/prepare for this release:
    - baseline tests (always from baseline plan)
    - execution set (baseline or file overlay existing_tests_to_execute if present)
    - baseline skip governance (FILE overlay): existing_tests_to_skip removes tests from execution set
    - AI candidates decisions (FILE overlay only):
        ACCEPTED / REJECTED / PENDING
    - missing tests (new_tests_to_create) (FILE overlay only)
    - if RUN overlay (Pattern A): candidates are shown as PENDING (no persisted decisions)
    """
    base = get_test_plan(plan_key)
    if base is None:
        raise HTTPException(status_code=404, detail={"message": f"Unknown plan_key: {plan_key}"})

    overlay_name = (overlay or "").strip() or None

    # Baseline tests always computed from baseline plan (stable reference point)
    baseline_tests: List[str] = _as_list_str(base.get("tests"))

    overlay_kind: Optional[str] = None
    plan: Dict[str, Any] = dict(base)

    # ─────────────────────────────────────────────────────────────
    # Resolve plan view according to overlay
    # ─────────────────────────────────────────────────────────────
    if overlay_name is None:
        overlay_kind = None
        plan = dict(base)

    elif _is_run_overlay_name(overlay_name):
        overlay_kind = "run"
        run_doc = _load_run_doc(overlay_name)
        if run_doc:
            run_overlay = _compute_run_overlay_for_plan(base, run_doc)
            plan = _merge_overlay_into_plan(base, run_overlay)
        else:
            # Should not happen if _is_run_overlay_name is strict, but stay safe.
            plan = dict(base)

    else:
        overlay_kind = "file"
        try:
            merged = get_test_plan_with_overlay(plan_key, overlay_name=overlay_name)
            plan = merged if isinstance(merged, dict) else dict(base)
        except Exception:
            plan = dict(base)

    overlay_block = _as_dict(plan.get("overlay"))
    governance = _as_dict(plan.get("governance"))

    # ─────────────────────────────────────────────────────────────
    # Execution baseline:
    # - start from baseline tests
    # - if FILE overlay has explicit existing_tests_to_execute, use it instead
    # - then apply existing_tests_to_skip (FILE overlay only)
    # ─────────────────────────────────────────────────────────────
    tests_to_execute_set = set(baseline_tests)

    skipped_existing: List[str] = []
    if overlay_kind == "file":
        existing_to_execute = _as_list_str(overlay_block.get("existing_tests_to_execute"))
        if existing_to_execute:
            tests_to_execute_set = set(existing_to_execute)

        skipped_existing = _extract_skip_test_keys(overlay_block)
        if skipped_existing:
            tests_to_execute_set.difference_update(set(skipped_existing))

    # ─────────────────────────────────────────────────────────────
    # AI candidates
    # ─────────────────────────────────────────────────────────────
    accepted_ai: List[str] = []
    rejected_ai: List[str] = []
    pending_ai: List[str] = []

    if overlay_kind == "file":
        accepted_ai, rejected_ai, pending_ai = _extract_ai_decisions_from_file_overlay(plan)

    elif overlay_kind == "run":
        # No persisted decisions on RUN overlays: everything is pending by nature
        pending_ai = _extract_candidate_keys_from_run_overlay(plan)

    # ─────────────────────────────────────────────────────────────
    # Missing tests: only meaningful for FILE overlays (G4 enrich/governance)
    # ─────────────────────────────────────────────────────────────
    missing_tests: List[Any] = []
    if overlay_kind == "file":
        missing_tests = _as_list(overlay_block.get("new_tests_to_create"))

    # ─────────────────────────────────────────────────────────────
    # Effective execution set:
    # - existing tests to execute (after skip)
    # - accepted AI candidates (treated as "included")
    # ─────────────────────────────────────────────────────────────
    effective_set = set(tests_to_execute_set)
    for ck in accepted_ai:
        effective_set.add(ck)

    effective_tests = sorted(effective_set)

    return {
        "data": {
            "plan_key": plan_key,
            "overlay": overlay_name,
            "overlay_kind": overlay_kind,
            "status": _overlay_status(plan),
            "summary": {
                "baseline_tests": len(baseline_tests),
                "accepted_ai": len(accepted_ai),
                "rejected_ai": len(rejected_ai),
                "pending_ai": len(pending_ai),
                "missing_tests": len(missing_tests),
                "skipped_existing": len(skipped_existing),  # new (non-breaking)
                "effective_total": len(effective_tests),
            },
            "tests_to_execute": effective_tests,
            "tests_excluded": rejected_ai,
            "tests_pending": pending_ai,
            "tests_missing": missing_tests,
            "tests_skipped": skipped_existing,  # new (non-breaking)
            "traceability": {
                "prompt_hash": governance.get("prompt_hash"),
                "run_jira_key": governance.get("run_jira_key"),
                "signals": governance.get("signals", []),
            },
        },
        "meta": {
            "plan_key": plan_key,
            "overlay": overlay_name,
            "overlay_kind": overlay_kind,
        },
        "errors": [],
    }
