# backend/routes/test_plans_routes.py
from __future__ import annotations

import logging
from typing import Optional, List, Dict, Any, Iterable

from fastapi import APIRouter, HTTPException, Query

from backend.data_client.xray_client import (
    list_test_plans,
    get_test_plan_with_overlay,
    load_test_plans_overlay,
    save_test_plans_overlay,
    xray_plans_overlay_file,  # optional (only if you want to detect overlay existence)
)
from backend.data_client.xray_client import get_xray_tests_for_issue

logger = logging.getLogger("qa-test-plan-agent")

router = APIRouter(prefix="/api/test-plans", tags=["test-plans"])


def _overlay_status(plan: dict) -> str:
    gov = plan.get("governance") if isinstance(plan.get("governance"), dict) else {}
    status = (gov or {}).get("status")
    return status or "NOT_ANALYZED"


def _safe_list(value: Any) -> List[Any]:
    """
    Fixes Pylance warning:
    - If value is None -> []
    - If not a list -> []
    """
    return value if isinstance(value, list) else []


@router.get("")
def api_list_test_plans(overlay: Optional[str] = Query(default=None)):
    """
    List baseline test plans and indicate if an overlay exists for each plan key.

    If overlay is provided:
    - we merge overlay(governance/overlay) into each baseline plan when available
    - and we expose overlay_status

    If overlay is NOT provided:
    - we return baseline plans only
    - overlay_status = NOT_ANALYZED
      (Front can call again with overlay=promptA/promptB to compare.)
    """
    base = list_test_plans()

    if not overlay:
        return {
            "data": [{**p, "overlay_status": "NOT_ANALYZED"} for p in base],
            "meta": {"count": len(base), "overlay": None},
            "errors": [],
        }

    overlay_list = load_test_plans_overlay(overlay)
    overlay_by_key = {p.get("key"): p for p in overlay_list if isinstance(p.get("key"), str)}

    data = []
    for p in base:
        ok = overlay_by_key.get(p.get("key"))
        merged = dict(p)
        if ok:
            for k in ("overlay", "governance"):
                if k in ok:
                    merged[k] = ok.get(k)
        merged["overlay_status"] = _overlay_status(merged)
        data.append(merged)

    return {
        "data": data,
        "meta": {"count": len(data), "overlay": overlay},
        "errors": [],
    }


@router.get("/{plan_key}")
def api_get_test_plan(plan_key: str, overlay: Optional[str] = Query(default=None)):
    plan = get_test_plan_with_overlay(plan_key, overlay_name=overlay)
    if plan is None:
        raise HTTPException(status_code=404, detail={"message": f"Unknown plan_key: {plan_key}"})

    return {
        "data": {**plan, "overlay_status": _overlay_status(plan)},
        "meta": {"plan_key": plan_key, "overlay": overlay},
        "errors": [],
    }


def _compute_overlay_for_plan(base_plan: dict) -> dict:
    """
    MVP rules-based overlay (hackathon-friendly).
    G4 pourra remplacer cette fonction par une version plus sophistiquée (LLM + heuristiques).
    """
    plan_key = base_plan.get("key")

    jira_keys: List[str] = [x for x in _safe_list(base_plan.get("jira_keys")) if isinstance(x, str)]

    # --- FIX VSCode/Pylance reportOptionalIterable ---
    # base_plan.get("tests") can be None => set(None) raises "None is not iterable"
    baseline_tests = set(x for x in _safe_list(base_plan.get("tests")) if isinstance(x, str))

    existing_to_execute: List[str] = []
    existing_to_skip: List[dict] = []
    new_to_create: List[dict] = []

    for jk in jira_keys:
        tests = get_xray_tests_for_issue(jk)
        xray_keys = {t.key for t in tests}

        # Baseline tests relevant to this issue (pattern: TEST-401-1 etc)
        issue_num = jk.split("-")[-1]
        expected_prefix = f"TEST-{issue_num}-"

        baseline_for_issue = [t for t in baseline_tests if t.startswith(expected_prefix)]

        for tkey in baseline_for_issue:
            if tkey in xray_keys:
                existing_to_execute.append(tkey)
            else:
                existing_to_skip.append(
                    {
                        "test_key": tkey,
                        "reason": "missing_in_xray",
                        "evidence": f"Not found under {jk} in tests_by_requirement.json",
                    }
                )

        if len(tests) == 0:
            new_to_create.append(
                {
                    "jira_key": jk,
                    "title": f"{jk} – Missing coverage: create baseline regression test",
                    "tags": ["regression"],
                    "priority": "HIGH",
                    "given": "A user is authenticated and has access to the QA Test Management Portal",
                    "when": "The user executes the feature described in the Jira story",
                    "then": "The expected outcome matches acceptance criteria and errors are handled cleanly",
                }
            )

        # Outdated detection (simple demo): keyword "outdated" in summary/steps
        for t in tests:
            txt = f"{t.summary}\n{t.steps or ''}".lower()
            if "outdated" in txt and t.key in baseline_tests:
                existing_to_skip.append(
                    {
                        "test_key": t.key,
                        "reason": "outdated_test",
                        "evidence": "Contains keyword 'outdated' in summary/steps",
                    }
                )

    # Remove duplicates while preserving order
    def _dedup(seq: List[Any]) -> List[Any]:
        seen = set()
        out = []
        for x in seq:
            k = x if isinstance(x, str) else (x.get("test_key") if isinstance(x, dict) else str(x))
            if k in seen:
                continue
            seen.add(k)
            out.append(x)
        return out

    existing_to_execute = _dedup(existing_to_execute)
    existing_to_skip = _dedup(existing_to_skip)

    # Governance MVP
    status = "REVIEW" if (existing_to_skip or new_to_create) else "AUTO"
    signals: List[str] = []
    if existing_to_skip:
        signals.append(f"skip_count:{len(existing_to_skip)}")
    if new_to_create:
        signals.append(f"new_tests_to_create:{len(new_to_create)}")

    return {
        "key": plan_key,
        "governance": {"status": status, "signals": signals},
        "overlay": {
            "existing_tests_to_execute": existing_to_execute,
            "existing_tests_to_skip": existing_to_skip,
            "new_tests_to_create": new_to_create,
        },
    }


@router.post("/{plan_key}/enrich")
def api_enrich_test_plan(
    plan_key: str,
    overlay: str = Query(default="promptA", description="Overlay name (e.g. promptA, promptB)"),
):
    base = get_test_plan_with_overlay(plan_key, overlay_name=None)
    if base is None:
        raise HTTPException(status_code=404, detail={"message": f"Unknown plan_key: {plan_key}"})

    # compute overlay plan record
    overlay_plan = _compute_overlay_for_plan(base)

    # load file overlay list, upsert by key, save
    overlay_list = load_test_plans_overlay(overlay)
    out: List[dict] = []
    replaced = False
    for p in overlay_list:
        if (p.get("key") or "").strip() == plan_key:
            out.append(overlay_plan)
            replaced = True
        else:
            out.append(p)
    if not replaced:
        out.append(overlay_plan)

    save_test_plans_overlay(overlay, out)

    merged = get_test_plan_with_overlay(plan_key, overlay_name=overlay)
    return {"data": merged, "meta": {"plan_key": plan_key, "overlay": overlay}, "errors": []}
