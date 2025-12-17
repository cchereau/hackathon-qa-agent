# backend/routes/viewer_routes.py
"""Read-only viewer endpoints to avoid black-box demos.

Expose mocked source data (Jira/Xray/Bitbucket) and the effective prompt.

T0 addition:
- XRAY "preview" endpoint that shows a consolidated view:
    baseline tests (tests_by_requirement.json)
  + AI candidate tests (from junction run artifact US-xxx.run.json)

This is visualization only (no writes to baseline files).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

from backend.data_client.bitbucket_client import get_bitbucket_changes_for_issue
from backend.data_client.jira_client import get_jira_issue
from backend.data_client.xray_client import get_xray_tests_for_issue
from backend.llm_client.llm_agent import SYSTEM_PROMPT, _build_prompt
from backend.llm_client.models import XrayTest
from backend.utils import JUNCTION_RUNS_DIR, load_json_file, sha256_text

router = APIRouter(tags=["viewer"])


# ─────────────────────────────────────────────────────────────
# Small safe helpers (viewer must not 500)
# ─────────────────────────────────────────────────────────────
def _as_dict(x: Any) -> Dict[str, Any]:
    return x if isinstance(x, dict) else {}


def _as_list(x: Any) -> List[Any]:
    return x if isinstance(x, list) else []


def _safe_model_dump(obj: Any) -> Any:
    """Return obj.model_dump() if available, else raw obj, else {}."""
    if obj is None:
        return {}
    md = getattr(obj, "model_dump", None)
    if callable(md):
        try:
            return md()
        except Exception:
            return {}
    return obj


def _short_hash(h: str, n: int = 10) -> str:
    s = (h or "").strip()
    return s[:n] if s else ""


# ─────────────────────────────────────────────────────────────
# Jira / Xray / Bitbucket Viewer
# ─────────────────────────────────────────────────────────────
@router.get("/api/jira/issue/{jira_key}")
def viewer_jira_issue(jira_key: str):
    try:
        issue = get_jira_issue(jira_key)  # fallback-friendly if absent
        data = _safe_model_dump(issue)
        return {"data": data, "meta": {"jira_key": jira_key}, "errors": []}
    except Exception as e:
        return {
            "data": {},
            "meta": {"jira_key": jira_key},
            "errors": [{"message": "Failed to load Jira issue", "detail": str(e)}],
        }


@router.get("/api/xray/tests/{jira_key}")
def viewer_xray_tests(jira_key: str):
    """Legacy endpoint (baseline only). Kept for backward compatibility."""
    try:
        tests = get_xray_tests_for_issue(jira_key) or []
        payload = [_safe_model_dump(t) for t in tests]
        return {"data": payload, "meta": {"jira_key": jira_key, "count": len(payload)}, "errors": []}
    except Exception as e:
        return {
            "data": [],
            "meta": {"jira_key": jira_key, "count": 0},
            "errors": [{"message": "Failed to load Xray tests", "detail": str(e)}],
        }


@router.get("/api/bitbucket/changes/{jira_key}")
def viewer_bitbucket_changes(jira_key: str):
    try:
        changes = get_bitbucket_changes_for_issue(jira_key) or []
        payload = [_safe_model_dump(c) for c in changes]
        return {"data": payload, "meta": {"jira_key": jira_key, "count": len(payload)}, "errors": []}
    except Exception as e:
        return {
            "data": [],
            "meta": {"jira_key": jira_key, "count": 0},
            "errors": [{"message": "Failed to load Bitbucket changes", "detail": str(e)}],
        }


# ─────────────────────────────────────────────────────────────
# XRAY Preview (baseline + candidates from run artifact)
# ─────────────────────────────────────────────────────────────
def _safe_load_run(jira_key: str) -> Optional[Dict[str, Any]]:
    """
    Load a junction run artifact if present.

    Expected path:
      mocks/junction/runs/<jira_key>.run.json
    """
    p = Path(JUNCTION_RUNS_DIR) / f"{jira_key}.run.json"
    if not p.is_file():
        return None
    try:
        doc = load_json_file(p)
        return doc if isinstance(doc, dict) else None
    except Exception:
        return None


def _extract_run_provenance(run_doc: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Normalize provenance fields for UI consistency."""
    rd = run_doc or {}
    prov = rd.get("provenance")
    if not isinstance(prov, dict):
        prov = {}

    prompt_hash = prov.get("prompt_hash")
    if not isinstance(prompt_hash, str):
        prompt_hash = ""

    generated_at = prov.get("generated_at")
    if not isinstance(generated_at, str) or not generated_at.strip():
        ts = rd.get("timestamp")
        generated_at = ts if isinstance(ts, str) and ts.strip() else ""

    schema_id = prov.get("schema_id")
    if not isinstance(schema_id, str):
        schema_id = ""

    ph = prompt_hash.strip()

    return {
        "prompt_hash": ph,
        "prompt_hash_short": _short_hash(ph, 10) or None,
        "generated_at": generated_at.strip() or None,
        "schema_id": schema_id.strip() or None,
        "run_present": bool(run_doc),
    }


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

    tags: List[str] = ["AI_CANDIDATE"]

    # Keep tags UI-friendly: store only short hash in tags
    phs = _short_hash(prompt_hash, 10)
    if phs:
        tags.append(f"PROMPT={phs}")

    prio = str(sug.get("priority") or "").strip().upper()
    if prio:
        tags.append(f"PRIORITY={prio}")

    typ = str(sug.get("type") or "").strip().lower()
    if typ:
        tags.append(f"TYPE={typ}")

    mapped = sug.get("mapped_existing_test_key")
    if isinstance(mapped, str) and mapped.strip():
        tags.append(f"MAPPED={mapped.strip()}")

    return XrayTest(key=key, summary=title, steps=steps, tags=tags)


@router.get("/api/xray/preview/{jira_key}")
def viewer_xray_preview(jira_key: str):
    """Consolidated view for G1/G2 (baseline + candidates from junction run).

    - baseline_tests: real tests from mocks/xray/tests_by_requirement.json
    - candidate_tests: preview-only tests derived from the exported run (if any)
    - consolidated_tests: baseline + candidates (preview)

    No writes. Safe if run doesn't exist.
    """
    try:
        baseline_tests = get_xray_tests_for_issue(jira_key) or []
    except Exception:
        baseline_tests = []

    run_doc = _safe_load_run(jira_key)
    prov = _extract_run_provenance(run_doc)
    prompt_hash = str(prov.get("prompt_hash") or "")

    suggestions = (run_doc or {}).get("suggestions")
    if not isinstance(suggestions, list):
        suggestions = []

    candidate_tests: List[XrayTest] = []
    for idx, s in enumerate(suggestions):
        if isinstance(s, dict):
            candidate_tests.append(_suggestion_to_candidate_test(jira_key, idx, s, prompt_hash))

    consolidated: List[XrayTest] = [*baseline_tests, *candidate_tests]

    baseline_dump = [_safe_model_dump(t) for t in baseline_tests]
    cand_dump = [_safe_model_dump(t) for t in candidate_tests]
    cons_dump = [_safe_model_dump(t) for t in consolidated]

    return {
        "data": {
            "jira_key": jira_key,
            "baseline_tests": baseline_dump,
            "candidate_tests": cand_dump,
            "consolidated_tests": cons_dump,
            "provenance": {
                "run_present": bool(prov.get("run_present")),
                "generated_at": prov.get("generated_at"),
                "prompt_hash": prov.get("prompt_hash") or None,
                "prompt_hash_short": prov.get("prompt_hash_short"),
                "schema_id": prov.get("schema_id"),
            },
        },
        "meta": {
            "jira_key": jira_key,
            "counts": {
                "baseline": len(baseline_dump),
                "candidates": len(cand_dump),
                "consolidated": len(cons_dump),
            },
        },
        "errors": [],
    }


# ─────────────────────────────────────────────────────────────
# Prompt viewer (traceability without run export)
# ─────────────────────────────────────────────────────────────
@router.get("/api/llm/prompt/{jira_key}")
def viewer_llm_prompt(jira_key: str):
    """
    Return the effective prompt used by the agent.

    UI traceability:
    - Include prompt_hash and schema_id so the inspector can display traceability
      without relying on run export.
    """
    try:
        issue = get_jira_issue(jira_key)
        tests = get_xray_tests_for_issue(jira_key) or []
        changes = get_bitbucket_changes_for_issue(jira_key) or []

        # Build prompt (must be deterministic)
        user_prompt = _build_prompt(issue, tests, changes)

        prompt_hash = sha256_text((SYSTEM_PROMPT or "") + "\n\n" + (user_prompt or ""))
        schema_id = "TPA-V1"

        return {
            "data": {
                "jira_key": jira_key,
                "prompt_hash": prompt_hash,
                "prompt_hash_short": _short_hash(prompt_hash, 10) or None,
                "schema_id": schema_id,
                "system_prompt": SYSTEM_PROMPT,
                "user_prompt": user_prompt,
            },
            "meta": {"jira_key": jira_key},
            "errors": [],
        }
    except Exception as e:
        return {
            "data": {
                "jira_key": jira_key,
                "prompt_hash": "",
                "prompt_hash_short": None,
                "schema_id": "TPA-V1",
                "system_prompt": SYSTEM_PROMPT,
                "user_prompt": "",
            },
            "meta": {"jira_key": jira_key},
            "errors": [{"message": "Failed to build prompt", "detail": str(e)}],
        }
