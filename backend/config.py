# backend/config.py
"""
Central configuration for the hackathon backend.

Design goals:
- Always load .env from the repository root in a deterministic way
- Support switching LLM providers (mock / openai / internal) via LLM_PROVIDER
- Keep secrets out of logs (provide "safe" diagnostics)
- Avoid URL confusion: base URL vs endpoint path (OpenAI returns 404 if misused)
"""

from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv


# ---------------------------------------------------------------------
# 1) Repo root discovery + .env loading (robust)
# ---------------------------------------------------------------------
def _find_repo_root(start: Path) -> Path:
    """
    Walk upwards until we find a folder that looks like the repository root.
    Markers: .env, pyproject.toml, README.md
    """
    markers = (".env", "pyproject.toml", "README.md")
    for p in [start, *start.parents]:
        if any((p / m).exists() for m in markers):
            return p
    # Fallback: assume backend/ is directly under repo root
    return start.parents[1]


REPO_ROOT = _find_repo_root(Path(__file__).resolve())
load_dotenv(dotenv_path=REPO_ROOT / ".env", override=False)


# ---------------------------------------------------------------------
# 2) LLM Provider switch
# ---------------------------------------------------------------------
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "mock").strip().lower()
# Allowed: mock | openai | internal
if LLM_PROVIDER not in {"mock", "openai", "internal"}:
    raise RuntimeError(
        f"Invalid LLM_PROVIDER='{LLM_PROVIDER}'. Expected mock|openai|internal."
    )


# ---------------------------------------------------------------------
# 3) Common LLM settings
# ---------------------------------------------------------------------
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini").strip()
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))


# ---------------------------------------------------------------------
# 4) OpenAI settings (LLM_PROVIDER=openai)
#
# IMPORTANT:
# - OPENAI_BASE_URL must be the BASE (e.g. https://api.openai.com/v1)
# - OPENAI_CHAT_PATH must be the PATH (e.g. /chat/completions)
# ---------------------------------------------------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()
OPENAI_CHAT_PATH = os.getenv("OPENAI_CHAT_PATH", "/chat/completions").strip()

# Normalize path
if OPENAI_CHAT_PATH and not OPENAI_CHAT_PATH.startswith("/"):
    OPENAI_CHAT_PATH = f"/{OPENAI_CHAT_PATH}"


# ---------------------------------------------------------------------
# 5) Internal LLMaaS settings (LLM_PROVIDER=internal)
#
# Same pattern: base URL + path
# If your internal gateway already exposes the full endpoint in LLM_BASE_URL,
# set LLM_CHAT_PATH="" and keep LLM_BASE_URL as the full endpoint.
# Otherwise:
#   LLM_BASE_URL=https://.../v1
#   LLM_CHAT_PATH=/chat/completions
# ---------------------------------------------------------------------
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "").strip()
LLM_CHAT_PATH = os.getenv("LLM_CHAT_PATH", "").strip()  # optional; can be empty
LLM_API_TOKEN = os.getenv("LLM_API_TOKEN", "").strip()

if LLM_CHAT_PATH and not LLM_CHAT_PATH.startswith("/"):
    LLM_CHAT_PATH = f"/{LLM_CHAT_PATH}"


# ---------------------------------------------------------------------
# 6) Provider validation helpers (used by LLMClient)
# ---------------------------------------------------------------------
def validate_llm_config() -> None:
    """
    Validate required settings for the selected provider.
    - mock: no requirements
    - openai: requires OPENAI_API_KEY
    - internal: requires LLM_BASE_URL (token often required, kept soft)
    """
    if LLM_PROVIDER == "openai":
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is empty (LLM_PROVIDER=openai).")
        if not OPENAI_BASE_URL:
            raise RuntimeError("OPENAI_BASE_URL is empty (LLM_PROVIDER=openai).")

    if LLM_PROVIDER == "internal":
        if not LLM_BASE_URL:
            raise RuntimeError("LLM_BASE_URL is empty (LLM_PROVIDER=internal).")
        # Token can be optional in some setups; keep it soft:
        # if not LLM_API_TOKEN:
        #     raise RuntimeError("LLM_API_TOKEN is empty (LLM_PROVIDER=internal).")


def config_diag_safe() -> dict:
    """
    Safe diagnostics (no secrets).
    Useful for /api/diag/config endpoint in main.py.
    """
    return {
        "repo_root": str(REPO_ROOT),
        "llm_provider": LLM_PROVIDER,
        "llm_model": LLM_MODEL,
        "llm_timeout_seconds": LLM_TIMEOUT_SECONDS,
        # OpenAI info (safe)
        "openai_base_url": OPENAI_BASE_URL if LLM_PROVIDER == "openai" else None,
        "openai_chat_path": OPENAI_CHAT_PATH if LLM_PROVIDER == "openai" else None,
        "has_openai_key": bool(OPENAI_API_KEY),
        # Internal info (safe)
        "internal_base_url": LLM_BASE_URL if LLM_PROVIDER == "internal" else None,
        "internal_chat_path": LLM_CHAT_PATH if LLM_PROVIDER == "internal" else None,
        "has_internal_token": bool(LLM_API_TOKEN),
    }
