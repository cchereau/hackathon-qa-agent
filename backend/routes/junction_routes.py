# backend/routes/junction_routes.py
"""Junction endpoints (minimal T0) to connect G1/G2 outputs to G4.

T0 principles:
- Baselines stay unchanged (mocks/xray/*).
- G1/G2 exports *suggestions only* into mocks/junction/runs/.
- Prompt versions are archived by hash so G1 can iterate and roll back.
- G4 consumes a *snapshot* (mocks/junction/snapshots/) to work independently.

This module intentionally avoids introducing complex workflow/state.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.llm_client.llm_agent import SYSTEM_PROMPT, _build_prompt
from backend.data_client.jira_client import get_jira_issue
from backend.data_client.xray_client import get_xray_tests_for_issue
from backend.data_client.bitbucket_client import get_bitbucket_changes_for_issue
from backend.utils import (
    JUNCTION_RUNS_DIR,
    JUNCTION_SNAPSHOTS_DIR,
    PROMPT_REGISTRY_FILE,
    PROMPT_STORE_DIR,
    load_json_file,
    save_json_file,
    sha256_text,
)

router = APIRouter(tags=["junction"])


# ----------------------------------------------------------------------
# Models
# ----------------------------------------------------------------------
class ExportRunRequest(BaseModel):
    """Payload coming from Issue Generator (G1/G2)."""

    markdown: str = Field("", description="Rendered Markdown produced by the agent.")
    suggestions: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="JSON suggestions (suggestions-only) produced by the agent.",
    )
    raw_context: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional debug context (issue/tests/changes) as seen by the front.",
    )
    schema_id: str = Field("g2/schema", description="Logical schema id (T0).")
    schema_hash: Optional[str] = Field(
        default=None,
        description="Optional schema hash computed client-side; server will compute if missing.",
    )
    provider: str = Field("mock", description="Provider label (mock|internal|openai).")
    model: str = Field("", description="Model label if available.")


class RunSummary(BaseModel):
    jira_key: str
    generated_at: str
    prompt_hash: str
    schema_hash: str
    path: str


# ----------------------------------------------------------------------
# Prompt registry helpers
# ----------------------------------------------------------------------
def _utc_iso_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def _load_prompt_registry() -> Dict[str, Any]:
    if PROMPT_REGISTRY_FILE.is_file():
        try:
            data = load_json_file(PROMPT_REGISTRY_FILE)
            if isinstance(data, dict):
                return data
        except Exception:
            # Registry corruption shouldn't kill the hackathon; we'll rebuild minimal.
            pass
    return {"active": {"prompt_id": "g1/prompt", "latest_hash": None}, "prompts": {}}


def _save_prompt_registry(reg: Dict[str, Any]) -> None:
    save_json_file(PROMPT_REGISTRY_FILE, reg)


def _archive_prompt_if_new(prompt_id: str, system_prompt: str, user_prompt: str) -> str:
    """Compute hash and archive prompt content if this hash isn't known."""

    combined = system_prompt + "\n\n---\n\n" + user_prompt
    prompt_hash = sha256_text(combined)

    reg = _load_prompt_registry()
    reg.setdefault("active", {})
    reg.setdefault("prompts", {})

    if prompt_hash not in reg["prompts"]:
        created_at = _utc_iso_now()
        PROMPT_STORE_DIR.mkdir(parents=True, exist_ok=True)
        # Use hex only filename (strip 'sha256:')
        filename = prompt_hash.replace("sha256:", "") + ".json"
        prompt_file = PROMPT_STORE_DIR / filename

        save_json_file(
            prompt_file,
            {
                "prompt_hash": prompt_hash,
                "prompt_id": prompt_id,
                "created_at": created_at,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
            },
        )

        reg["prompts"][prompt_hash] = {
            "created_at": created_at,
            "label": prompt_id,
            "source": "code",
            "files": [str(prompt_file)],
        }

    # Always update active/latest
    reg["active"]["prompt_id"] = prompt_id
    reg["active"]["latest_hash"] = prompt_hash
    _save_prompt_registry(reg)

    return prompt_hash


def _compute_schema_hash(schema_id: str, suggestions: List[Dict[str, Any]]) -> str:
    """T0: hash a minimal schema descriptor.

    We don't enforce a full JSON Schema here; we just version what matters:
    - schema_id
    - keys used in suggestions
    """
    keys: List[str] = []
    for s in suggestions or []:
        if isinstance(s, dict):
            keys.extend(list(s.keys()))
    keys = sorted(set(keys))
    canonical = {"schema_id": schema_id, "suggestion_keys": keys}
    return sha256_text(str(canonical))


# ----------------------------------------------------------------------
# Effective prompt builder (same as viewer, but usable for export)
# ----------------------------------------------------------------------
def _get_effective_prompts(jira_key: str) -> Dict[str, str]:
    issue = get_jira_issue(jira_key)
    tests = get_xray_tests_for_issue(jira_key)
    changes = get_bitbucket_changes_for_issue(jira_key)

    user_prompt = _build_prompt(issue, tests, changes)
    return {"system_prompt": SYSTEM_PROMPT, "user_prompt": user_prompt}


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------
@router.post("/api/junction/runs/{jira_key}")
def export_run(jira_key: str, payload: ExportRunRequest):
    """Persist a run artifact (suggestions-only) and archive prompt versions.

    This is the *minimal junction* between Issue Generator (G1/G2) and Test Plans (G4).
    """
    try:
        prompts = _get_effective_prompts(jira_key)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail={"source": "prompt", "message": f"Unable to build prompt for {jira_key}", "reason": str(exc)},
        )

    prompt_hash = _archive_prompt_if_new(
        prompt_id="g1/prompt",
        system_prompt=prompts["system_prompt"],
        user_prompt=prompts["user_prompt"],
    )

    schema_hash = payload.schema_hash or _compute_schema_hash(payload.schema_id, payload.suggestions)

    run_file: Path = JUNCTION_RUNS_DIR / f"{jira_key}.run.json"
    overwrote = run_file.is_file()
    prev: Optional[Dict[str, Any]] = None
    if overwrote:
        try:
            prev = load_json_file(run_file)
        except Exception:
            prev = None

    generated_at = _utc_iso_now()

    run_doc = {
        "jira_key": jira_key,
        "generated_at": generated_at,
        "provenance": {
            "prompt_id": "g1/prompt",
            "prompt_hash": prompt_hash,
            "schema_id": payload.schema_id,
            "schema_hash": schema_hash,
            "provider": payload.provider,
            "model": payload.model,
        },
        "markdown": payload.markdown or "",
        "suggestions": payload.suggestions or [],
        "raw_context": payload.raw_context,
    }

    save_json_file(run_file, run_doc)

    return {
        "data": {
            "jira_key": jira_key,
            "run_path": str(run_file),
            "generated_at": generated_at,
            "prompt_hash": prompt_hash,
            "schema_hash": schema_hash,
            "overwrote": overwrote,
            "previous_generated_at": (prev or {}).get("generated_at"),
        },
        "meta": {"jira_key": jira_key},
        "errors": [],
    }


@router.get("/api/junction/runs")
def list_runs():
    """List available run artifacts."""
    items: List[RunSummary] = []
    if JUNCTION_RUNS_DIR.is_dir():
        for p in sorted(JUNCTION_RUNS_DIR.glob("*.run.json")):
            try:
                doc = load_json_file(p)
                prov = (doc or {}).get("provenance") or {}
                items.append(
                    RunSummary(
                        jira_key=str((doc or {}).get("jira_key") or p.stem.replace(".run", "")),
                        generated_at=str((doc or {}).get("generated_at") or ""),
                        prompt_hash=str(prov.get("prompt_hash") or ""),
                        schema_hash=str(prov.get("schema_hash") or ""),
                        path=str(p),
                    )
                )
            except Exception:
                continue

    return {"data": [i.model_dump() for i in items], "meta": {"count": len(items)}, "errors": []}


@router.get("/api/junction/snapshots/g12")
def get_g12_snapshot():
    """Return the upstream snapshot consumed by G4 (if present)."""
    snap = JUNCTION_SNAPSHOTS_DIR / "g12_suggestions.snapshot.json"
    if not snap.is_file():
        # Return a valid empty snapshot for UX stability.
        return {
            "data": {"snapshot_id": "empty", "generated_at": None, "items": []},
            "meta": {"path": str(snap)},
            "errors": [],
        }

    try:
        data = load_json_file(snap)
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"source": "snapshot", "message": "Snapshot unreadable", "reason": str(exc)})

    return {"data": data, "meta": {"path": str(snap)}, "errors": []}
