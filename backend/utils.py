# backup/utils.py
"""
Utility helpers used across the backend (now located in backup/).

Goals:
- Load .env deterministically (repo root)
- Resolve absolute paths reliably on all OS (always relative to repo root)
- Provide JSON-file helpers with clear errors
- Avoid "double hackathon/" path bugs

IMPORTANT:
- The repository root is the folder that contains `.env`, `backend/`, `frontend/`, `mocks/`, etc.
- We do NOT append "/hackathon" to the root; your repo is already that folder.
"""

import os
import json
import pathlib
from typing import Any, Dict, Union

# ----------------------------------------------------------------------
# 1) Repo root resolution + .env loading
# ----------------------------------------------------------------------
def _find_repo_root(start: pathlib.Path) -> pathlib.Path:
    """
    Walk up from `start` to find a folder that looks like the project root.
    We use markers that exist in your repo: `.env` or `pyproject.toml` or `README.md`.
    Fallback: parent of this file.
    """
    markers = {".env", "pyproject.toml", "README.md"}
    for p in [start, *start.parents]:
        if any((p / m).exists() for m in markers):
            return p
    return start.parents[1]  # safe fallback for backup/utils.py layout


try:
    from dotenv import load_dotenv  # type: ignore

    REPO_ROOT = _find_repo_root(pathlib.Path(__file__).resolve())
    load_dotenv(dotenv_path=REPO_ROOT / ".env")
except Exception:  # pragma: no cover – dotenv optional
    REPO_ROOT = _find_repo_root(pathlib.Path(__file__).resolve())


# ----------------------------------------------------------------------
# 2) Helper: env var (or default) -> absolute Path
# ----------------------------------------------------------------------
def _env_path(key: str, default: Union[str, pathlib.Path]) -> pathlib.Path:
    """
    Return a pathlib.Path guaranteed to be absolute.
    - If env var `key` is missing, fallback to `default`
    - Relative values are interpreted as relative to REPO_ROOT
    """
    raw = os.getenv(key, str(default)).strip()
    p = pathlib.Path(raw)

    if not p.is_absolute():
        p = REPO_ROOT / p

    return p.resolve()


# ----------------------------------------------------------------------
# 3) Constants imported across the codebase
# ----------------------------------------------------------------------
# Project root: repo root (no extra /hackathon!)
PROJECT_ROOT: pathlib.Path = REPO_ROOT

# Mock data root – defaults to <repo>/mocks
MOCK_ROOT: pathlib.Path = _env_path("MOCK_ROOT", "mocks")

# Sub-folders (note: your tree is "bitbulket" in mocks; we support both)
JIRA_MOCK_DIR: pathlib.Path = _env_path("JIRA_MOCK_DIR", MOCK_ROOT / "jira")
XRAY_MOCK_DIR: pathlib.Path = _env_path("XRAY_MOCK_DIR", MOCK_ROOT / "xray")

# Support both spellings: bitbucket vs bitbulket
_default_bb_dir = MOCK_ROOT / ("bitbucket" if (MOCK_ROOT / "bitbucket").exists() else "bitbulket")
BITBUCKET_MOCK_DIR: pathlib.Path = _env_path("BITBUCKET_MOCK_DIR", _default_bb_dir)

# Individual JSON files
JIRA_ISSUES_FILE: pathlib.Path = _env_path("JIRA_ISSUES_FILE", JIRA_MOCK_DIR / "issues.json")
XRAY_TESTS_FILE: pathlib.Path = _env_path("XRAY_TESTS_FILE", XRAY_MOCK_DIR / "tests_by_requirement.json")
XRAY_PLANS_FILE: pathlib.Path = _env_path("XRAY_PLANS_FILE", XRAY_MOCK_DIR / "test_plans.json")
BITBUCKET_CHANGES_FILE: pathlib.Path = _env_path(
    "BITBUCKET_CHANGES_FILE", BITBUCKET_MOCK_DIR / "changes_by_jira_key.json"
)


# ----------------------------------------------------------------------
# 4) JSON loader (raises clear FileNotFoundError)
# ----------------------------------------------------------------------
def load_json_file(path: pathlib.Path) -> Dict[str, Any]:
    """Read a JSON file and return its content as a dict."""
    if not path.is_file():
        raise FileNotFoundError(f"Mock file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


# ----------------------------------------------------------------------
# 5) Debug helper (optional)
# ----------------------------------------------------------------------
def debug_print_env() -> None:  # pragma: no cover
    """Print all computed constants – handy while developing."""
    print("=== ENV DEBUG ===")
    print(f"CWD: {pathlib.Path.cwd()}")
    for name in [
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
    ]:
        print(f"{name}: {globals()[name]}")
    print("=================")


# ----------------------------------------------------------------------
# Exported symbols – makes `from backup.utils import …` tidy
# ----------------------------------------------------------------------
__all__ = [
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
    "load_json_file",
    "debug_print_env",
]
