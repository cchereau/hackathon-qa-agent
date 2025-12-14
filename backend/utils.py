"""
Utility helpers used across the backend.

Goals:
- Load .env deterministically (repo root)
- Resolve absolute paths reliably on all OS (always relative to repo root)
- Provide JSON-file helpers with clear errors

IMPORTANT:
- The repository root is the folder that contains `.env`, `backend/`, `frontend/`, `mocks/`, etc.
- We do NOT append "/hackathon" to the root; your repo is already that folder.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
from typing import Any, Dict, Union


# ----------------------------------------------------------------------
# 1) Repo root resolution + .env loading
# ----------------------------------------------------------------------
def _find_repo_root(start: pathlib.Path) -> pathlib.Path:
    """
    Walk up from `start` until we find a folder containing:
    - backend/
    - mocks/
    or a `.env` marker.
    """
    start = start.resolve()
    for p in [start] + list(start.parents):
        if (p / "backend").is_dir() and (p / "mocks").is_dir():
            return p
        if (p / ".env").is_file():
            return p
    # Fallback: assume 2 levels up from backend/...
    return start.parents[1] if len(start.parents) >= 2 else start


REPO_ROOT = _find_repo_root(pathlib.Path(__file__).parent)
PROJECT_ROOT = REPO_ROOT  # alias kept for backward compatibility

MOCK_ROOT = REPO_ROOT / "mocks"
JIRA_MOCK_DIR = MOCK_ROOT / "jira"
XRAY_MOCK_DIR = MOCK_ROOT / "xray"
BITBUCKET_MOCK_DIR = MOCK_ROOT / "bitbucket"


JIRA_ISSUES_FILE = JIRA_MOCK_DIR / "issues.json"
XRAY_TESTS_FILE = XRAY_MOCK_DIR / "tests_by_requirement.json"
XRAY_PLANS_FILE = XRAY_MOCK_DIR / "test_plans.json"
BITBUCKET_CHANGES_FILE = BITBUCKET_MOCK_DIR / "changes_by_jira_key.json"

PROMPT_DIR = MOCK_ROOT / "prompts"
PROMPT_STORE_DIR = PROMPT_DIR / "prompts"
PROMPT_REGISTRY_FILE = PROMPT_DIR / "prompt_registry.json"


def debug_print_env() -> Dict[str, str]:
    """
    Helper for /diag endpoints: return a shallow view of env vars.
    """
    keys = [
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_MODEL",
        "LLM_PROVIDER",
    ]
    return {k: os.getenv(k, "") for k in keys}


# ----------------------------------------------------------------------
# 2) Xray overlays file naming
# ----------------------------------------------------------------------
def xray_plans_overlay_file(overlay_name: str) -> pathlib.Path:
    """
    Overlay path stored next to XRAY_PLANS_FILE.

    Format:
      test_plans_enriched.<overlay_name>.json
    """
    name = (overlay_name or "").strip()
    if not name:
        name = "default"
    return XRAY_MOCK_DIR / f"test_plans_enriched.{name}.json"


# ----------------------------------------------------------------------
# 3) JSON file I/O helpers
# ----------------------------------------------------------------------
PathLike = Union[str, pathlib.Path]


def load_json_file(path: PathLike) -> Any:
    """
    Read JSON from disk.

    Note:
    - returns Any (dict or list), so callers should validate types.
    """
    p = pathlib.Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Mock file not found: {p}")

    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json_file(path: PathLike, content: Any) -> None:
    """
    Write JSON deterministically (UTF-8, pretty-print for hackathon readability).
    """
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(content, f, indent=2, ensure_ascii=False)


# ----------------------------------------------------------------------
# 4) JUNCTION + PROMPTS (new, added without breaking legacy)
# ----------------------------------------------------------------------
JUNCTION_DIR = MOCK_ROOT / "junction"
JUNCTION_RUNS_DIR = JUNCTION_DIR / "runs"
JUNCTION_SNAPSHOTS_DIR = JUNCTION_DIR / "snapshots"

PROMPTS_DIR = MOCK_ROOT / "prompts"
PROMPTS_STORE_DIR = PROMPTS_DIR / "prompts"
PROMPT_REGISTRY_FILE = PROMPTS_DIR / "prompt_registry.json"

G12_SNAPSHOT_FILE = JUNCTION_SNAPSHOTS_DIR / "g12_suggestions.snapshot.json"


def ensure_dirs() -> None:
    """
    Create required folders if missing. Safe to call multiple times.
    """
    for d in [
        JUNCTION_RUNS_DIR,
        JUNCTION_SNAPSHOTS_DIR,
        PROMPTS_STORE_DIR,
    ]:
        d.mkdir(parents=True, exist_ok=True)


def sha256_text(text: str) -> str:
    """
    Return a stable sha256 identifier formatted as 'sha256:<hex>'.
    """
    h = hashlib.sha256()
    h.update((text or "").encode("utf-8"))
    return f"sha256:{h.hexdigest()}"


def prompt_store_file(prompt_hash: str) -> pathlib.Path:
    """
    File path where a prompt version is stored.
    """
    safe = (prompt_hash or "").replace("sha256:", "").strip()
    return PROMPTS_STORE_DIR / f"{safe}.json"


def run_file(jira_key: str) -> pathlib.Path:
    """
    Run file path for a given Jira key.
    """
    key = (jira_key or "").strip()
    return JUNCTION_RUNS_DIR / f"{key}.run.json"


__all__ = [
    # legacy exports (MUST keep)
    "REPO_ROOT",
    "PROJECT_ROOT",
    "MOCK_ROOT",
    "JIRA_MOCK_DIR",
    "XRAY_MOCK_DIR",
    "BITBUCKET_MOCK_DIR",
    "JIRA_ISSUES_FILE",
    "XRAY_TESTS_FILE",
    "XRAY_PLANS_FILE",
    "BITBUCKET_CHANGES_FILE",
    "xray_plans_overlay_file",
    "load_json_file",
    "save_json_file",
    "debug_print_env",
    # new exports (junction/prompts)
    "JUNCTION_DIR",
    "JUNCTION_RUNS_DIR",
    "JUNCTION_SNAPSHOTS_DIR",
    "PROMPTS_DIR",
    "PROMPT_STORE_DIR",
    "PROMPT_REGISTRY_FILE",
    "G12_SNAPSHOT_FILE",
    "ensure_dirs",
    "sha256_text",
    "prompt_store_file",
    "run_file",
]
