# backend/routes/test_plans_routes.py
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, cast

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.data_client.xray_client import (
    get_test_plan,
    get_test_plan_with_overlay,
    list_test_plans,
    load_test_plans_overlay,
    save_test_plans_overlay,
    xray_plans_overlay_file,
)
from backend.utils import JUNCTION_RUNS_DIR, load_json_file

logger = logging.getLogger("qa-test-plan-agent")

router = APIRouter(prefix="/api/test-plans", tags=["test-plans"])

_RUN_KEY_RE = re.compile(r"^US-\d{3,}$")

# Candidate decision values persisted in file overlays (T0+)
DEC_PENDING = "PENDING"
DEC_ACCEPT = "ACCEPTED"
DEC_REJECT = "REJECTED"
_ALLOWED_DECISIONS = {DEC_PENDING, DEC_ACCEPT, DEC_REJECT}


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────
def _safe_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _as_dict(x: Any) -> Dict[str, Any]:
    return x if isinstance(x, dict) else {}


def _as_str(x: Any) -> str:
    return x.strip() if isinstance(x, str) else ""


def _as_list(x: Any) -> List[Any]:
    return x if isinstance(x, list) else []


def _as_list_dict(x: Any) -> List[Dict[str, Any]]:
    return [i for i in _as_list(x) if isinstance(i, dict)]


def _overlay_status(plan: Dict[str, Any]) -> str:
    gov = _as_dict(plan.get("governance"))
    status = gov.get("status")
    return status if isinstance(status, str) and status else "NOT_ANALYZED"


def _is_run_overlay_name(name: Optional[str]) -> bool:
    if not name:
        return False
    n = name.strip()
    if not _RUN_KEY_RE.match(n):
        return False
    return (JUNCTION_RUNS_DIR / f"{n}.run.json").exists()


def _list_run_overlays() -> List[Dict[str, Any]]:
    """
    Returns runs present under mocks/junction/runs/*.run.json as overlays (Pattern A).
    """
    out: List[Dict[str, Any]] = []
    if not JUNCTION_RUNS_DIR.exists():
        return out

    for p in sorted(JUNCTION_RUNS_DIR.glob("*.run.json")):
        name = p.stem.replace(".run", "")  # US-402.run -> US-402
        if not _RUN_KEY_RE.match(name):
            continue
        try:
            doc = load_json_file(p)
            docd = _as_dict(doc)
            prov = _as_dict(docd.get("provenance"))
            prompt_hash = prov.get("prompt_hash")

            label = f"{name} (run)"
            if prompt_hash:
                label = f"{name} (run, {str(prompt_hash)[:8]}…)"
            out.append({"name": name, "kind": "run", "label": label})
        except Exception:
            out.append({"name": name, "kind": "run", "label": f"{name} (run)"})

    return out


def _list_file_overlays() -> List[Dict[str, Any]]:
    """
    Returns overlays present under mocks/xray/test_plans_enriched.<name>.json
    """
    out: List[Dict[str, Any]] = []

    sample = xray_plans_overlay_file("promptA")
    folder = sample.parent if sample else Path(".")
    if not folder.exists():
        return out

    for p in sorted(folder.glob("test_plans_enriched.*.json")):
        parts = p.name.split(".")
        if len(parts) < 3:
            continue
        name = parts[-2].strip()
        if not name:
            continue
        out.append({"name": name, "kind": "file", "label": f"{name} (file)"})

    return out


def _load_run_doc(run_key: str) -> Optional[Dict[str, Any]]:
    p = JUNCTION_RUNS_DIR / f"{run_key}.run.json"
    if not p.exists():
        return None
    raw = load_json_file(p)
    return raw if isinstance(raw, dict) else None


def _compute_run_overlay_for_plan(base_plan: Dict[str, Any], run_doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pattern A:
      - A run (US-xxx.run.json) is treated as a computed overlay (read-only).
      - We compute an overlay ONLY for plans that contain that US key in jira_keys.
      - Output is merged in-memory (no baseline writes).
    """
    plan_key = base_plan.get("key")

    jira_keys = [x for x in _safe_list(base_plan.get("jira_keys")) if isinstance(x, str)]
    run_key = _as_str(run_doc.get("jira_key"))

    if not run_key or run_key not in jira_keys:
        return {
            "key": plan_key,
            "governance": {"status": "NOT_ANALYZED", "signals": ["no_run_match"]},
            "overlay": {"candidate_tests": []},
        }

    prov = _as_dict(run_doc.get("provenance"))
    prompt_hash = prov.get("prompt_hash")

    generated_at = prov.get("generated_at")
    if not isinstance(generated_at, str) or not generated_at:
        ts = run_doc.get("timestamp")
        generated_at = ts if isinstance(ts, str) and ts else None

    suggestions_list = _as_list_dict(run_doc.get("suggestions"))

    candidates: List[Dict[str, Any]] = []
    idx = 1

    for s in suggestions_list:
        title = _as_str(s.get("title"))
        if not title:
            continue

        candidate_key = f"CAND-{run_key}-{idx:03d}"
        idx += 1

        prio_raw = s.get("priority")
        typ_raw = s.get("type")

        candidates.append(
            {
                "candidate_key": candidate_key,
                "title": title,
                "priority": prio_raw if isinstance(prio_raw, str) and prio_raw else "MEDIUM",
                "type": typ_raw if isinstance(typ_raw, str) and typ_raw else "functional",
                "mapped_existing_test_key": s.get("mapped_existing_test_key"),
            }
        )

    status = "REVIEW" if candidates else "AUTO"
    signals: List[str] = [f"run:{run_key}", f"candidates:{len(candidates)}"]
    if prompt_hash:
        signals.append(f"prompt:{str(prompt_hash)[:8]}")

    return {
        "key": plan_key,
        "governance": {
            "status": status,
            "signals": signals,
            "source": "run",
            "run_jira_key": run_key,
            "prompt_hash": prompt_hash,
            "generated_at": generated_at,
        },
        "overlay": {
            "candidate_tests": candidates,
            "note": "Computed overlay (Pattern A): read-only preview from G1/G2 run.",
        },
    }


def _merge_overlay_into_plan(base: Dict[str, Any], overlay_plan: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for k in ("governance", "overlay"):
        if k in overlay_plan:
            merged[k] = overlay_plan.get(k)
    for k in ("summary", "jira_keys", "tests"):
        if k in overlay_plan:
            merged[k] = overlay_plan.get(k)
    return merged


def _compute_file_overlay_for_plan(base_plan: Dict[str, Any]) -> Dict[str, Any]:
    """
    Existing (T0 MVP) rules-based overlay generator used by G4 when they click "Enrich".
    This writes to file overlays (promptA/promptB/etc).
    """
    plan_key = base_plan.get("key")
    jira_keys: List[str] = [x for x in _safe_list(base_plan.get("jira_keys")) if isinstance(x, str)]
    baseline_tests = set(x for x in _safe_list(base_plan.get("tests")) if isinstance(x, str))

    existing_to_execute: List[str] = []
    existing_to_skip: List[dict] = []
    new_to_create: List[dict] = []

    for jk in jira_keys:
        issue_num = jk.split("-")[-1]
        expected_prefix = f"TEST-{issue_num}-"
        baseline_for_issue = [t for t in baseline_tests if t.startswith(expected_prefix)]

        for tkey in baseline_for_issue:
            existing_to_execute.append(tkey)

        if not baseline_for_issue:
            new_to_create.append(
                {
                    "jira_key": jk,
                    "title": f"{jk} – Missing coverage: create regression test",
                    "tags": ["regression"],
                    "priority": "HIGH",
                    "given": "A user is authenticated and has access to the QA Test Management Portal",
                    "when": "The user executes the feature described in the Jira story",
                    "then": "The expected outcome matches acceptance criteria and errors are handled cleanly",
                }
            )

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

    existing_to_execute = cast(List[str], _dedup(existing_to_execute))
    existing_to_skip = cast(List[dict], _dedup(existing_to_skip))

    status = "REVIEW" if (existing_to_skip or new_to_create) else "AUTO"
    signals: List[str] = []
    if existing_to_skip:
        signals.append(f"skip_count:{len(existing_to_skip)}")
    if new_to_create:
        signals.append(f"new_tests_to_create:{len(new_to_create)}")

    return {
        "key": plan_key,
        "governance": {"status": status, "signals": signals, "source": "g4_enrich"},
        "overlay": {
            "existing_tests_to_execute": existing_to_execute,
            "existing_tests_to_skip": existing_to_skip,
            "new_tests_to_create": new_to_create,
        },
    }


def _find_overlay_plan(overlay_list: List[dict], plan_key: str) -> Optional[dict]:
    for p in overlay_list:
        if isinstance(p, dict) and (_as_str(p.get("key")) == plan_key):
            return p
    return None


def _upsert_overlay_plan(overlay_list: List[dict], plan_key: str, overlay_plan: dict) -> List[dict]:
    out: List[dict] = []
    replaced = False
    for p in overlay_list:
        if isinstance(p, dict) and (_as_str(p.get("key")) == plan_key):
            out.append(overlay_plan)
            replaced = True
        else:
            out.append(p)
    if not replaced:
        out.append(overlay_plan)
    return out


def _run_candidates_to_governable_candidates(run_overlay: Dict[str, Any]) -> Tuple[List[dict], Dict[str, Any]]:
    """
    Convert run overlay (computed) to governable candidates persisted in a FILE overlay.
    """
    gov = _as_dict(run_overlay.get("governance"))
    ov = _as_dict(run_overlay.get("overlay"))

    run_key = gov.get("run_jira_key")
    prompt_hash = gov.get("prompt_hash")
    generated_at = gov.get("generated_at")

    candidates = _as_list_dict(ov.get("candidate_tests"))

    out: List[dict] = []
    for c in candidates:
        ck = c.get("candidate_key")
        title = c.get("title")
        if not isinstance(ck, str) or not ck:
            continue
        out.append(
            {
                "candidate_key": ck,
                "title": title if isinstance(title, str) else "",
                "priority": c.get("priority") if isinstance(c.get("priority"), str) else "MEDIUM",
                "type": c.get("type") if isinstance(c.get("type"), str) else "functional",
                "mapped_existing_test_key": c.get("mapped_existing_test_key"),
                "decision": DEC_PENDING,
                "rationale": "",
                "source_run": run_key,
                "prompt_hash": prompt_hash,
                "generated_at": generated_at,
            }
        )

    meta = {"source_run": run_key, "prompt_hash": prompt_hash, "generated_at": generated_at}
    return out, meta


# ─────────────────────────────────────────────────────────────
# API
# ─────────────────────────────────────────────────────────────
@router.get("/overlays")
def api_list_overlays():
    """
    Returns overlays usable from the UI:
      - file overlays: test_plans_enriched.<name>.json
      - run overlays:  US-xxx.run.json (Pattern A, computed/read-only)
    """
    file_overlays = _list_file_overlays()
    run_overlays = _list_run_overlays()

    by_name: Dict[str, Dict[str, Any]] = {}
    for o in file_overlays + run_overlays:
        if o.get("name"):
            by_name[str(o["name"])] = o

    data = list(by_name.values())

    def _sort_key(o: Dict[str, Any]):
        kind = (o.get("kind") or "").lower()
        kind_rank = 0 if kind == "file" else 1 if kind == "run" else 2
        return (kind_rank, (o.get("name") or "").lower())

    data.sort(key=_sort_key)

    return {"data": data, "meta": {"count": len(data)}, "errors": []}


@router.get("")
def api_list_test_plans(overlay: Optional[str] = Query(default=None)):
    """
    List baseline test plans.

    If overlay is provided:
      - if overlay is a file overlay: merge from file (non-destructive)
      - if overlay is a run overlay (US-xxx): compute overlay per plan on the fly (Pattern A)
    """
    base = list_test_plans()

    if not overlay:
        return {
            "data": [{**p, "overlay_status": "NOT_ANALYZED"} for p in base],
            "meta": {"count": len(base), "overlay": None},
            "errors": [],
        }

    if _is_run_overlay_name(overlay):
        run_doc = _load_run_doc(overlay)
        if not run_doc:
            return {
                "data": [{**p, "overlay_status": "NOT_ANALYZED"} for p in base],
                "meta": {"count": len(base), "overlay": overlay, "overlay_kind": "run"},
                "errors": [],
            }

        out: List[Dict[str, Any]] = []
        for p in base:
            ov = _compute_run_overlay_for_plan(p, run_doc)
            merged = _merge_overlay_into_plan(p, ov)
            merged["overlay_status"] = _overlay_status(merged)
            out.append(merged)

        return {"data": out, "meta": {"count": len(out), "overlay": overlay, "overlay_kind": "run"}, "errors": []}

    overlay_list = load_test_plans_overlay(overlay)
    overlay_by_key = {p.get("key"): p for p in overlay_list if isinstance(p, dict) and isinstance(p.get("key"), str)}

    out: List[Dict[str, Any]] = []
    for p in base:
        ok = overlay_by_key.get(p.get("key"))
        merged = dict(p)
        if ok:
            merged = _merge_overlay_into_plan(merged, cast(Dict[str, Any], ok))
        merged["overlay_status"] = _overlay_status(merged)
        out.append(merged)

    return {"data": out, "meta": {"count": len(out), "overlay": overlay, "overlay_kind": "file"}, "errors": []}


@router.get("/{plan_key}")
def api_get_test_plan(plan_key: str, overlay: Optional[str] = Query(default=None)):
    """
    Get a plan.

    If overlay is a run overlay => compute overlay for this plan (Pattern A).
    Else => standard file overlay merge.
    """
    base = get_test_plan(plan_key)
    if base is None:
        raise HTTPException(status_code=404, detail={"message": f"Unknown plan_key: {plan_key}"})

    if not overlay:
        return {"data": {**base, "overlay_status": "NOT_ANALYZED"}, "meta": {"plan_key": plan_key, "overlay": None}, "errors": []}

    if _is_run_overlay_name(overlay):
        run_doc = _load_run_doc(overlay)
        if not run_doc:
            return {"data": {**base, "overlay_status": "NOT_ANALYZED"}, "meta": {"plan_key": plan_key, "overlay": overlay, "overlay_kind": "run"}, "errors": []}

        ov = _compute_run_overlay_for_plan(base, run_doc)
        merged = _merge_overlay_into_plan(base, ov)
        merged["overlay_status"] = _overlay_status(merged)
        return {"data": merged, "meta": {"plan_key": plan_key, "overlay": overlay, "overlay_kind": "run"}, "errors": []}

    merged = get_test_plan_with_overlay(plan_key, overlay_name=overlay)
    merged_final: Dict[str, Any] = merged if isinstance(merged, dict) else base
    return {
        "data": {**merged_final, "overlay_status": _overlay_status(merged_final)},
        "meta": {"plan_key": plan_key, "overlay": overlay, "overlay_kind": "file"},
        "errors": [],
    }


@router.post("/{plan_key}/enrich")
def api_enrich_test_plan(
    plan_key: str,
    overlay: str = Query(default="promptA", description="File overlay name (e.g. promptA, promptB)"),
):
    """Compute and persist a file-based overlay for a plan.

    Guardrail (Pattern A):
    - run overlays (US-xxx) are computed and read-only.
    - this endpoint only writes to a file overlay.
    """
    if _is_run_overlay_name(overlay):
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Overlay is a run (computed, read-only). Select a file overlay to persist G4 governance.",
                "overlay": overlay,
            },
        )

    base = get_test_plan(plan_key)
    if base is None:
        raise HTTPException(status_code=404, detail={"message": f"Unknown plan_key: {plan_key}"})

    overlay_plan = _compute_file_overlay_for_plan(base)

    overlay_list = load_test_plans_overlay(overlay)
    overlay_list = _upsert_overlay_plan(overlay_list, plan_key, overlay_plan)

    save_test_plans_overlay(overlay, overlay_list)

    merged = get_test_plan_with_overlay(plan_key, overlay_name=overlay)
    return {"data": merged, "meta": {"plan_key": plan_key, "overlay": overlay, "overlay_kind": "file"}, "errors": []}


# ─────────────────────────────────────────────────────────────
# T0+ : Apply run overlay → file overlay (persist candidates)
# ─────────────────────────────────────────────────────────────
@router.post("/{plan_key}/apply-run")
def api_apply_run_to_file_overlay(
    plan_key: str,
    run: str = Query(..., description="Run key US-xxx (computed overlay source)"),
    overlay: str = Query(default="promptA", description="Target FILE overlay name (persist decisions)"),
):
    """
    T0+:
    - Read computed run overlay (US-xxx) for that plan
    - Persist candidates into FILE overlay as overlay.ai_candidates[] with decision=PENDING
    - Does NOT touch baseline
    """
    if not _RUN_KEY_RE.match(run or ""):
        raise HTTPException(status_code=400, detail={"message": "Invalid run key", "run": run})

    if _is_run_overlay_name(overlay):
        raise HTTPException(status_code=400, detail={"message": "Target overlay must be a FILE overlay name", "overlay": overlay})

    base = get_test_plan(plan_key)
    if base is None:
        raise HTTPException(status_code=404, detail={"message": f"Unknown plan_key: {plan_key}"})

    run_doc = _load_run_doc(run)
    if not run_doc:
        raise HTTPException(status_code=404, detail={"message": f"Run not found: {run}", "run": run})

    # Compute run overlay for this plan
    run_overlay = _compute_run_overlay_for_plan(base, run_doc)

    # Persist even if empty
    candidates, meta = _run_candidates_to_governable_candidates(run_overlay)
    run_key = meta.get("source_run")
    prompt_hash = meta.get("prompt_hash")

    # Load existing file overlay plan (if any)
    overlay_list = load_test_plans_overlay(overlay)
    existing_plan_opt = _find_overlay_plan(overlay_list, plan_key)

    existing_plan: Dict[str, Any] = (
        cast(Dict[str, Any], existing_plan_opt)
        if isinstance(existing_plan_opt, dict)
        else {"key": plan_key, "governance": {}, "overlay": {}}
    )

    gov: Dict[str, Any] = _as_dict(existing_plan.get("governance"))
    ov: Dict[str, Any] = _as_dict(existing_plan.get("overlay"))

    # Merge strategy:
    # - keep existing ai_candidates from other runs
    # - replace candidates for this run (prefix + source_run)
    existing_ai = _as_list_dict(ov.get("ai_candidates"))

    kept: List[dict] = []
    prefix = f"CAND-{run}-"
    for c in existing_ai:
        ck = c.get("candidate_key")
        src_run = c.get("source_run")

        if isinstance(ck, str) and ck.startswith(prefix):
            continue
        if isinstance(src_run, str) and src_run == run:
            continue
        kept.append(c)

    new_ai = kept + candidates
    ov["ai_candidates"] = new_ai

    # Governance: mark REVIEW if there are candidates
    signals_any = _as_list(gov.get("signals"))
    signals: List[str] = [s for s in signals_any if isinstance(s, str)]
    signals.append(f"applied_run:{run}")
    signals.append(f"ai_candidates:{len(new_ai)}")
    if prompt_hash:
        signals.append(f"prompt:{str(prompt_hash)[:8]}")

    gov.update(
        {
            "status": "REVIEW" if new_ai else "AUTO",
            "source": "g4_apply_run",
            "signals": signals,
            "run_jira_key": run_key,
            "prompt_hash": prompt_hash,
        }
    )

    persisted_overlay_plan: Dict[str, Any] = {"key": plan_key, "governance": gov, "overlay": ov}

    overlay_list = _upsert_overlay_plan(overlay_list, plan_key, persisted_overlay_plan)
    save_test_plans_overlay(overlay, overlay_list)

    merged = get_test_plan_with_overlay(plan_key, overlay_name=overlay)
    return {
        "data": merged,
        "meta": {"plan_key": plan_key, "overlay": overlay, "overlay_kind": "file", "applied_run": run},
        "errors": [],
    }


# ─────────────────────────────────────────────────────────────
# T0+ : Decide (accept/reject/reset) on a candidate in FILE overlay
# ─────────────────────────────────────────────────────────────
class CandidateDecisionIn(BaseModel):
    candidate_key: str
    decision: str  # ACCEPTED | REJECTED | PENDING
    rationale: Optional[str] = ""


@router.post("/{plan_key}/candidates/decision")
def api_set_candidate_decision(
    plan_key: str,
    body: CandidateDecisionIn,
    overlay: str = Query(default="promptA", description="FILE overlay name where decisions are persisted"),
):
    if _is_run_overlay_name(overlay):
        raise HTTPException(
            status_code=400,
            detail={"message": "Decisions can only be persisted in FILE overlays.", "overlay": overlay},
        )

    ck = _as_str(body.candidate_key)
    if not ck:
        raise HTTPException(status_code=400, detail={"message": "candidate_key is required"})

    dec = _as_str(body.decision).upper()
    if dec not in _ALLOWED_DECISIONS:
        raise HTTPException(
            status_code=400,
            detail={"message": "Invalid decision", "decision": dec, "allowed": sorted(_ALLOWED_DECISIONS)},
        )

    overlay_list = load_test_plans_overlay(overlay)
    plan_overlay_opt = _find_overlay_plan(overlay_list, plan_key)
    if not isinstance(plan_overlay_opt, dict):
        raise HTTPException(
            status_code=404,
            detail={"message": "Plan overlay not found in file overlay", "plan_key": plan_key, "overlay": overlay},
        )

    plan_overlay: Dict[str, Any] = cast(Dict[str, Any], plan_overlay_opt)

    ov: Dict[str, Any] = _as_dict(plan_overlay.get("overlay"))
    ai: List[Dict[str, Any]] = _as_list_dict(ov.get("ai_candidates"))

    updated = False
    for c in ai:
        if _as_str(c.get("candidate_key")) == ck:
            c["decision"] = dec
            c["rationale"] = body.rationale if isinstance(body.rationale, str) else ""
            updated = True
            break

    if not updated:
        raise HTTPException(status_code=404, detail={"message": "candidate_key not found in overlay.ai_candidates", "candidate_key": ck})

    ov["ai_candidates"] = ai
    plan_overlay["overlay"] = ov

    # Governance signals + status
    gov: Dict[str, Any] = _as_dict(plan_overlay.get("governance"))

    signals_any = _as_list(gov.get("signals"))
    signals: List[str] = [s for s in signals_any if isinstance(s, str)]
    signals = [s for s in signals if not s.startswith("decisions:")]  # keep readable

    cnt_a = sum(1 for c in ai if c.get("decision") == DEC_ACCEPT)
    cnt_r = sum(1 for c in ai if c.get("decision") == DEC_REJECT)
    cnt_p = sum(1 for c in ai if c.get("decision") == DEC_PENDING)

    signals.append(f"decisions:accepted={cnt_a},rejected={cnt_r},pending={cnt_p}")

    gov["signals"] = signals
    gov["status"] = "REVIEW" if cnt_p > 0 else "AUTO"
    gov["source"] = gov.get("source") or "g4_decision"

    plan_overlay["governance"] = gov

    overlay_list = _upsert_overlay_plan(overlay_list, plan_key, plan_overlay)
    save_test_plans_overlay(overlay, overlay_list)

    merged = get_test_plan_with_overlay(plan_key, overlay_name=overlay)
    return {"data": merged, "meta": {"plan_key": plan_key, "overlay": overlay, "overlay_kind": "file"}, "errors": []}
