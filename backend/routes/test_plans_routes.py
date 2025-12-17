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

# Run overlays are US-xxx and must exist on disk as mocks/junction/runs/US-xxx.run.json
_RUN_KEY_RE = re.compile(r"^US-\d{3,}$")

# File overlay names are constrained to avoid path tricks and to keep UI predictable.
_FILE_OVERLAY_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Candidate decision values persisted in file overlays (T0+)
DEC_PENDING = "PENDING"
DEC_ACCEPT = "ACCEPTED"
DEC_REJECT = "REJECTED"
_ALLOWED_DECISIONS = {DEC_PENDING, DEC_ACCEPT, DEC_REJECT}

# Default FILE overlays exposed to UI even if not yet created on disk
_DEFAULT_FILE_OVERLAYS: List[Dict[str, str]] = [
    {"name": "promptA", "kind": "file", "label": "promptA (file)"},
    {"name": "promptB", "kind": "file", "label": "promptB (file)"},
    {"name": "coreA", "kind": "file", "label": "coreA (file)"},
    {"name": "governanceStrict", "kind": "file", "label": "governanceStrict (file)"},
]


# ─────────────────────────────────────────────────────────────
# Helpers (typed and defensive)
# ─────────────────────────────────────────────────────────────
def _as_dict(x: Any) -> Dict[str, Any]:
    return x if isinstance(x, dict) else {}


def _as_str(x: Any) -> str:
    return x.strip() if isinstance(x, str) else ""


def _as_list(x: Any) -> List[Any]:
    return x if isinstance(x, list) else []


def _as_list_str(x: Any) -> List[str]:
    return [i for i in _as_list(x) if isinstance(i, str)]


def _as_list_dict(x: Any) -> List[Dict[str, Any]]:
    return [i for i in _as_list(x) if isinstance(i, dict)]


def _dedup_keep_order(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for it in items:
        if it in seen:
            continue
        seen.add(it)
        out.append(it)
    return out


def _normalize_overlay_param(overlay: Optional[str]) -> Optional[str]:
    """
    Normalize overlay query param:
      - None or "" or whitespace => None
      - else stripped string
    """
    if overlay is None:
        return None
    ov = overlay.strip()
    return ov if ov else None


def _overlay_status(plan: Dict[str, Any]) -> str:
    gov = _as_dict(plan.get("governance"))
    status = gov.get("status")
    return status if isinstance(status, str) and status else "NOT_ANALYZED"


def _is_valid_file_overlay_name(name: Optional[str]) -> bool:
    if not name:
        return False
    n = name.strip()
    return bool(_FILE_OVERLAY_RE.match(n))


def _is_run_overlay_name(name: Optional[str]) -> bool:
    """
    A "run overlay" is identified by a US-xxx key and must exist on disk as:
      mocks/junction/runs/US-xxx.run.json
    """
    if not name:
        return False
    n = name.strip()
    if not _RUN_KEY_RE.match(n):
        return False
    return (JUNCTION_RUNS_DIR / f"{n}.run.json").exists()


def _safe_load_test_plans_overlay(name: str) -> List[dict]:
    """
    UI expects file overlays to be selectable even if not yet created.
    So: if overlay file is missing or cannot be loaded, return empty list.
    """
    if not _is_valid_file_overlay_name(name):
        raise HTTPException(status_code=400, detail={"message": "Invalid file overlay name", "overlay": name})

    try:
        overlay_list = load_test_plans_overlay(name)
        return overlay_list if isinstance(overlay_list, list) else []
    except FileNotFoundError:
        return []
    except Exception as e:
        logger.warning("Failed to load file overlay %s: %s", name, e)
        return []


def _safe_save_test_plans_overlay(name: str, overlay_list: List[dict]) -> None:
    if not _is_valid_file_overlay_name(name):
        raise HTTPException(status_code=400, detail={"message": "Invalid file overlay name", "overlay": name})
    save_test_plans_overlay(name, overlay_list)


def _load_run_doc(run_key: str) -> Optional[Dict[str, Any]]:
    p = JUNCTION_RUNS_DIR / f"{run_key}.run.json"
    if not p.exists():
        return None
    raw = load_json_file(p)
    return raw if isinstance(raw, dict) else None


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
            if isinstance(prompt_hash, str) and prompt_hash:
                label = f"{name} (run, {str(prompt_hash)[:8]}…)"
            out.append({"name": name, "kind": "run", "label": label})
        except Exception:
            out.append({"name": name, "kind": "run", "label": f"{name} (run)"})

    return out


def _list_file_overlays() -> List[Dict[str, Any]]:
    """
    Returns overlays present under mocks/xray/test_plans_enriched.<name>.json
    PLUS default overlays even if not present yet (so UI is not hard-coded).
    """
    out: List[Dict[str, Any]] = []
    out.extend(_DEFAULT_FILE_OVERLAYS)

    sample = xray_plans_overlay_file("promptA")
    folder = sample.parent if isinstance(sample, Path) else Path(".")
    if folder.exists():
        for p in sorted(folder.glob("test_plans_enriched.*.json")):
            parts = p.name.split(".")
            if len(parts) < 3:
                continue
            name = parts[-2].strip()
            if not name or not _is_valid_file_overlay_name(name):
                continue
            out.append({"name": name, "kind": "file", "label": f"{name} (file)"})

    # Deduplicate by name (discovered file can override default label if same name)
    by_name: Dict[str, Dict[str, Any]] = {}
    for o in out:
        n = o.get("name")
        if not n:
            continue
        by_name[str(n)] = o

    data = list(by_name.values())
    data.sort(key=lambda x: (str(x.get("name") or "").lower()))
    return data


def _merge_overlay_into_plan(base: Dict[str, Any], overlay_plan: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge only the overlay-specific fields into the baseline plan.
    We keep this explicit to avoid accidental baseline drift.
    """
    merged = dict(base)
    for k in ("governance", "overlay"):
        if k in overlay_plan:
            merged[k] = overlay_plan.get(k)
    # If overlay plan contains enriched plan fields, allow them explicitly.
    for k in ("summary", "jira_keys", "tests"):
        if k in overlay_plan:
            merged[k] = overlay_plan.get(k)
    return merged


def _compute_run_overlay_for_plan(base_plan: Dict[str, Any], run_doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pattern A (computed, read-only):
      - A run (US-xxx.run.json) is treated as a computed overlay.
      - We compute an overlay ONLY for plans that contain that US key in jira_keys.
      - Output is merged in-memory (no baseline writes).
    """
    plan_key = base_plan.get("key")

    jira_keys = [x for x in _as_list_str(base_plan.get("jira_keys")) if x]
    run_key = _as_str(run_doc.get("jira_key"))

    if not run_key or run_key not in jira_keys:
        return {
            "key": plan_key,
            "governance": {"status": "NOT_ANALYZED", "signals": ["no_run_match"], "source": "run"},
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
    if isinstance(prompt_hash, str) and prompt_hash:
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


def _compute_file_overlay_for_plan(base_plan: Dict[str, Any]) -> Dict[str, Any]:
    """
    Rules-based overlay generator used by G4 when they click "Enrich".
    This writes to file overlays (promptA/promptB/coreA/etc).

    IMPORTANT:
      - Tests are named TEST-US-401-1 (not TEST-401-1)
      - Match baseline tests using prefix TEST-<JIRA_KEY>-  e.g. TEST-US-401-
    """
    plan_key = base_plan.get("key")
    jira_keys: List[str] = [x for x in _as_list_str(base_plan.get("jira_keys")) if x]
    baseline_tests = set([x for x in _as_list_str(base_plan.get("tests")) if x])

    existing_to_execute: List[str] = []
    existing_to_skip: List[dict] = []
    new_to_create: List[dict] = []

    for jk in jira_keys:
        expected_prefix = f"TEST-{jk}-"  # TEST-US-401-
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
                    "given": "A dealer or back-office user is authenticated in the Leasing Portal",
                    "when": "The user executes the business flow described in the Jira story",
                    "then": "The outcome matches acceptance criteria and edge cases are handled cleanly",
                }
            )

    existing_to_execute = _dedup_keep_order(existing_to_execute)

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
      - file overlays: test_plans_enriched.<name>.json (AND defaults promptA/promptB/coreA/...)
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
    overlay_name = _normalize_overlay_param(overlay)

    if not overlay_name:
        return {
            "data": [{**p, "overlay_status": "NOT_ANALYZED"} for p in base],
            "meta": {"count": len(base), "overlay": None, "overlay_kind": None},
            "errors": [],
        }

    if _is_run_overlay_name(overlay_name):
        run_doc = _load_run_doc(overlay_name)
        if not run_doc:
            return {
                "data": [{**p, "overlay_status": "NOT_ANALYZED"} for p in base],
                "meta": {"count": len(base), "overlay": overlay_name, "overlay_kind": "run"},
                "errors": [],
            }

        out: List[Dict[str, Any]] = []
        for p in base:
            ov = _compute_run_overlay_for_plan(p, run_doc)
            merged = _merge_overlay_into_plan(p, ov)
            merged["overlay_status"] = _overlay_status(merged)
            out.append(merged)

        return {"data": out, "meta": {"count": len(out), "overlay": overlay_name, "overlay_kind": "run"}, "errors": []}

    # file overlay
    overlay_list = _safe_load_test_plans_overlay(overlay_name)
    overlay_by_key = {p.get("key"): p for p in overlay_list if isinstance(p, dict) and isinstance(p.get("key"), str)}

    out: List[Dict[str, Any]] = []
    for p in base:
        ok = overlay_by_key.get(p.get("key"))
        merged = dict(p)
        if ok:
            merged = _merge_overlay_into_plan(merged, cast(Dict[str, Any], ok))
        merged["overlay_status"] = _overlay_status(merged)
        out.append(merged)

    return {"data": out, "meta": {"count": len(out), "overlay": overlay_name, "overlay_kind": "file"}, "errors": []}


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

    overlay_name = _normalize_overlay_param(overlay)

    if not overlay_name:
        merged = {**base, "overlay_status": "NOT_ANALYZED"}
        return {"data": merged, "meta": {"plan_key": plan_key, "overlay": None, "overlay_kind": None}, "errors": []}

    if _is_run_overlay_name(overlay_name):
        run_doc = _load_run_doc(overlay_name)
        if not run_doc:
            merged = {**base, "overlay_status": "NOT_ANALYZED"}
            return {"data": merged, "meta": {"plan_key": plan_key, "overlay": overlay_name, "overlay_kind": "run"}, "errors": []}

        ov = _compute_run_overlay_for_plan(base, run_doc)
        merged = _merge_overlay_into_plan(base, ov)
        merged["overlay_status"] = _overlay_status(merged)
        return {"data": merged, "meta": {"plan_key": plan_key, "overlay": overlay_name, "overlay_kind": "run"}, "errors": []}

    # file overlay (prefer the canonical merge function)
    if not _is_valid_file_overlay_name(overlay_name):
        raise HTTPException(status_code=400, detail={"message": "Invalid file overlay name", "overlay": overlay_name})

    merged = get_test_plan_with_overlay(plan_key, overlay_name=overlay_name)
    merged_final: Dict[str, Any] = merged if isinstance(merged, dict) else base
    merged_final = {**merged_final, "overlay_status": _overlay_status(merged_final)}
    return {"data": merged_final, "meta": {"plan_key": plan_key, "overlay": overlay_name, "overlay_kind": "file"}, "errors": []}


@router.post("/{plan_key}/enrich")
def api_enrich_test_plan(
    plan_key: str,
    overlay: str = Query(default="promptA", description="File overlay name (e.g. promptA, promptB, coreA)"),
):
    """
    Compute and persist a file-based overlay for a plan.

    Guardrail (Pattern A):
    - run overlays (US-xxx) are computed and read-only.
    - this endpoint only writes to a file overlay.
    """
    overlay_name = _normalize_overlay_param(overlay) or ""
    if _is_run_overlay_name(overlay_name):
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Overlay is a run (computed, read-only). Select a file overlay to persist G4 governance.",
                "overlay": overlay_name,
            },
        )

    if not _is_valid_file_overlay_name(overlay_name):
        raise HTTPException(status_code=400, detail={"message": "Invalid file overlay name", "overlay": overlay_name})

    base = get_test_plan(plan_key)
    if base is None:
        raise HTTPException(status_code=404, detail={"message": f"Unknown plan_key: {plan_key}"})

    overlay_plan = _compute_file_overlay_for_plan(base)

    overlay_list = _safe_load_test_plans_overlay(overlay_name)
    overlay_list = _upsert_overlay_plan(overlay_list, plan_key, overlay_plan)
    _safe_save_test_plans_overlay(overlay_name, overlay_list)

    merged = get_test_plan_with_overlay(plan_key, overlay_name=overlay_name)
    return {"data": merged, "meta": {"plan_key": plan_key, "overlay": overlay_name, "overlay_kind": "file"}, "errors": []}


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
    run_key = _normalize_overlay_param(run) or ""
    overlay_name = _normalize_overlay_param(overlay) or ""

    if not _RUN_KEY_RE.match(run_key):
        raise HTTPException(status_code=400, detail={"message": "Invalid run key", "run": run_key})

    if _is_run_overlay_name(overlay_name):
        raise HTTPException(status_code=400, detail={"message": "Target overlay must be a FILE overlay name", "overlay": overlay_name})

    if not _is_valid_file_overlay_name(overlay_name):
        raise HTTPException(status_code=400, detail={"message": "Invalid file overlay name", "overlay": overlay_name})

    base = get_test_plan(plan_key)
    if base is None:
        raise HTTPException(status_code=404, detail={"message": f"Unknown plan_key: {plan_key}"})

    run_doc = _load_run_doc(run_key)
    if not run_doc:
        raise HTTPException(status_code=404, detail={"message": f"Run not found: {run_key}", "run": run_key})

    run_overlay = _compute_run_overlay_for_plan(base, run_doc)
    candidates, meta = _run_candidates_to_governable_candidates(run_overlay)
    source_run = meta.get("source_run")
    prompt_hash = meta.get("prompt_hash")

    overlay_list = _safe_load_test_plans_overlay(overlay_name)
    existing_plan_opt = _find_overlay_plan(overlay_list, plan_key)

    existing_plan: Dict[str, Any] = (
        cast(Dict[str, Any], existing_plan_opt)
        if isinstance(existing_plan_opt, dict)
        else {"key": plan_key, "governance": {}, "overlay": {}}
    )

    gov: Dict[str, Any] = _as_dict(existing_plan.get("governance"))
    ov: Dict[str, Any] = _as_dict(existing_plan.get("overlay"))

    existing_ai = _as_list_dict(ov.get("ai_candidates"))

    kept: List[dict] = []
    prefix = f"CAND-{run_key}-"
    for c in existing_ai:
        ck = c.get("candidate_key")
        src_run = c.get("source_run")
        if isinstance(ck, str) and ck.startswith(prefix):
            continue
        if isinstance(src_run, str) and src_run == run_key:
            continue
        kept.append(c)

    new_ai = kept + candidates
    ov["ai_candidates"] = new_ai

    signals_any = _as_list(gov.get("signals"))
    signals: List[str] = [s for s in signals_any if isinstance(s, str)]
    signals.append(f"applied_run:{run_key}")
    signals.append(f"ai_candidates:{len(new_ai)}")
    if isinstance(prompt_hash, str) and prompt_hash:
        signals.append(f"prompt:{str(prompt_hash)[:8]}")
    signals = _dedup_keep_order(signals)

    # status: REVIEW if any pending candidates remain
    has_pending = any((_as_str(c.get("decision")).upper() == DEC_PENDING) for c in new_ai)
    gov.update(
        {
            "status": "REVIEW" if has_pending else "AUTO",
            "source": "g4_apply_run",
            "signals": signals,
            "run_jira_key": source_run,
            "prompt_hash": prompt_hash,
        }
    )

    persisted_overlay_plan: Dict[str, Any] = {"key": plan_key, "governance": gov, "overlay": ov}

    overlay_list = _upsert_overlay_plan(overlay_list, plan_key, persisted_overlay_plan)
    _safe_save_test_plans_overlay(overlay_name, overlay_list)

    merged = get_test_plan_with_overlay(plan_key, overlay_name=overlay_name)
    return {
        "data": merged,
        "meta": {"plan_key": plan_key, "overlay": overlay_name, "overlay_kind": "file", "applied_run": run_key},
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
    overlay_name = _normalize_overlay_param(overlay) or ""

    if _is_run_overlay_name(overlay_name):
        raise HTTPException(
            status_code=400,
            detail={"message": "Decisions can only be persisted in FILE overlays.", "overlay": overlay_name},
        )

    if not _is_valid_file_overlay_name(overlay_name):
        raise HTTPException(status_code=400, detail={"message": "Invalid file overlay name", "overlay": overlay_name})

    ck = _as_str(body.candidate_key)
    if not ck:
        raise HTTPException(status_code=400, detail={"message": "candidate_key is required"})

    dec = _as_str(body.decision).upper()
    if dec not in _ALLOWED_DECISIONS:
        raise HTTPException(
            status_code=400,
            detail={"message": "Invalid decision", "decision": dec, "allowed": sorted(_ALLOWED_DECISIONS)},
        )

    overlay_list = _safe_load_test_plans_overlay(overlay_name)
    plan_overlay_opt = _find_overlay_plan(overlay_list, plan_key)
    if not isinstance(plan_overlay_opt, dict):
        raise HTTPException(
            status_code=404,
            detail={"message": "Plan overlay not found in file overlay", "plan_key": plan_key, "overlay": overlay_name},
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
        raise HTTPException(
            status_code=404,
            detail={"message": "candidate_key not found in overlay.ai_candidates", "candidate_key": ck},
        )

    ov["ai_candidates"] = ai
    plan_overlay["overlay"] = ov

    gov: Dict[str, Any] = _as_dict(plan_overlay.get("governance"))

    signals_any = _as_list(gov.get("signals"))
    signals: List[str] = [s for s in signals_any if isinstance(s, str)]
    signals = [s for s in signals if not s.startswith("decisions:")]

    cnt_a = sum(1 for c in ai if _as_str(c.get("decision")).upper() == DEC_ACCEPT)
    cnt_r = sum(1 for c in ai if _as_str(c.get("decision")).upper() == DEC_REJECT)
    cnt_p = sum(1 for c in ai if _as_str(c.get("decision")).upper() == DEC_PENDING)

    signals.append(f"decisions:accepted={cnt_a},rejected={cnt_r},pending={cnt_p}")
    signals = _dedup_keep_order(signals)

    gov["signals"] = signals
    gov["status"] = "REVIEW" if cnt_p > 0 else "AUTO"
    gov["source"] = gov.get("source") or "g4_decision"

    plan_overlay["governance"] = gov

    overlay_list = _upsert_overlay_plan(overlay_list, plan_key, plan_overlay)
    _safe_save_test_plans_overlay(overlay_name, overlay_list)

    merged = get_test_plan_with_overlay(plan_key, overlay_name=overlay_name)
    return {"data": merged, "meta": {"plan_key": plan_key, "overlay": overlay_name, "overlay_kind": "file"}, "errors": []}
