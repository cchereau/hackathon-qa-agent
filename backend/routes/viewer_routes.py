# backend/routes/viewer_routes.py
"""Read-only viewer endpoints to avoid black-box demos.

Expose mocked source data (Jira/Xray/Bitbucket) and the effective prompt.

T0 addition:
- XRAY "preview" endpoint that shows a consolidated view:
    baseline tests (tests_by_requirement.json)
  + AI candidate tests (from junction run artifact US-xxx.run.json)

This is *visualization only* (no writes to baseline files).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

from backend.data_client.jira_client import get_jira_issue
from backend.data_client.xray_client import get_xray_tests_for_issue
from backend.data_client.bitbucket_client import get_bitbucket_changes_for_issue
from backend.llm_client.llm_agent import SYSTEM_PROMPT, _build_prompt
from backend.llm_client.models import XrayTest
from backend.utils import JUNCTION_RUNS_DIR, load_json_file

router = APIRouter(tags=["viewer"])


@router.get("/api/jira/issue/{jira_key}")
def viewer_jira_issue(jira_key: str):
    issue = get_jira_issue(jira_key)  # fallback-friendly if absent
    return {"data": issue.model_dump(), "meta": {"jira_key": jira_key}, "errors": []}


@router.get("/api/xray/tests/{jira_key}")
def viewer_xray_tests(jira_key: str):
    """Legacy endpoint (baseline only). Kept for backward compatibility."""
    tests = get_xray_tests_for_issue(jira_key)
    return {
        "data": [t.model_dump() for t in tests],
        "meta": {"jira_key": jira_key, "count": len(tests)},
        "errors": [],
    }


def _safe_load_run(jira_key: str) -> Optional[Dict[str, Any]]:
    p = Path(JUNCTION_RUNS_DIR) / f"{jira_key}.run.json"
    if not p.is_file():
        return None
    try:
        doc = load_json_file(p)
        return doc if isinstance(doc, dict) else None
    except Exception:
        return None


def _suggestion_to_candidate_test(jira_key: str, idx: int, sug: Dict[str, Any], prompt_hash: str) -> XrayTest:
    """Convert a G1/G2 suggestion into a preview-only XrayTest."""
    n = idx + 1
    key = f"CAND-{jira_key}-{n:03d}"
    title = str(sug.get("title") or "Untitled candidate test")
    given = str(sug.get("given") or "")
    when = str(sug.get("when") or "")
    then = str(sug.get("then") or "")

    steps_parts: List[str] = []
    if given:
        steps_parts.append(f"GIVEN: {given}")
    if when:
        steps_parts.append(f"WHEN: {when}")
    if then:
        steps_parts.append(f"THEN: {then}")
    steps = "\n".join(steps_parts) if steps_parts else None

    tags: List[str] = ["AI_CANDIDATE", f"PROMPT={prompt_hash}"] if prompt_hash else ["AI_CANDIDATE"]
    prio = str(sug.get("priority") or "").strip().upper()
    if prio:
        tags.append(f"PRIORITY={prio}")
    typ = str(sug.get("type") or "").strip().lower()
    if typ:
        tags.append(f"TYPE={typ}")

    mapped = sug.get("mapped_existing_test_key")
    if mapped:
        tags.append(f"MAPPED={mapped}")

    return XrayTest(key=key, summary=title, steps=steps, tags=tags)


@router.get("/api/xray/preview/{jira_key}")
def viewer_xray_preview(jira_key: str):
    """Consolidated view for G1/G2 (baseline + candidates from junction run).

    - baseline_tests: real tests from mocks/xray/tests_by_requirement.json
    - candidate_tests: preview-only tests derived from the latest exported run (if any)
    - consolidated_tests: baseline + candidates (preview)

    No writes. Safe if run doesn't exist.
    """

    baseline_tests = get_xray_tests_for_issue(jira_key)

    run_doc = _safe_load_run(jira_key)
    prov = (run_doc or {}).get("provenance") or {}
    prompt_hash = str(prov.get("prompt_hash") or "")
    generated_at = str((run_doc or {}).get("generated_at") or "")

    suggestions = (run_doc or {}).get("suggestions") or []
    if not isinstance(suggestions, list):
        suggestions = []

    candidate_tests: List[XrayTest] = []
    for idx, s in enumerate(suggestions):
        if isinstance(s, dict):
            candidate_tests.append(_suggestion_to_candidate_test(jira_key, idx, s, prompt_hash))

    consolidated: List[XrayTest] = [*baseline_tests, *candidate_tests]

    return {
        "data": {
            "jira_key": jira_key,
            "baseline_tests": [t.model_dump() for t in baseline_tests],
            "candidate_tests": [t.model_dump() for t in candidate_tests],
            "consolidated_tests": [t.model_dump() for t in consolidated],
            "provenance": {
                "run_present": bool(run_doc),
                "generated_at": generated_at or None,
                "prompt_hash": prompt_hash or None,
            },
        },
        "meta": {
            "jira_key": jira_key,
            "counts": {
                "baseline": len(baseline_tests),
                "candidates": len(candidate_tests),
                "consolidated": len(consolidated),
            },
        },
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
