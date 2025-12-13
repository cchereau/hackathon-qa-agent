# backend/routes/jira_project_routes.py
import json
from typing import List

from fastapi import APIRouter

from backend.utils import JIRA_ISSUES_FILE  # source de vérité chemins

router = APIRouter(prefix="/api/jira", tags=["jira"])


@router.get("/issue-keys")
def jira_issue_keys():
    """
    Retourne la liste des clés Jira trouvées dans le fichier mock Jira.
    Chaque entrée provient du champ: {"key": "PROJ-301"}.
    """
    if not JIRA_ISSUES_FILE.exists():
        return {
            "data": [],
            "meta": {"source": str(JIRA_ISSUES_FILE), "count": 0, "warning": "Jira issues file not found"},
            "errors": [],
        }

    try:
        raw = json.loads(JIRA_ISSUES_FILE.read_text(encoding="utf-8"))

        keys: List[str] = []

        # Supporte 2 formats:
        # A) dict indexé par clé: {"PROJ-301": {...}, ...}
        # B) liste d'issues: [{"key":"PROJ-301", ...}, ...]
        if isinstance(raw, dict) and "issues" not in raw:
            # Format A: keys = les clés du dict (le plus compatible avec ton jira_client actuel)
            for k in raw.keys():
                if isinstance(k, str) and "-" in k:
                    keys.append(k)
        else:
            # Format B
            issues = raw.get("issues", raw) if isinstance(raw, dict) else raw
            if isinstance(issues, list):
                for it in issues:
                    if isinstance(it, dict):
                        k = (it.get("key") or "").strip()
                        if k:
                            keys.append(k)

        # dédup + tri
        data = sorted(set(keys))

        return {
            "data": data,
            "meta": {"source": str(JIRA_ISSUES_FILE), "count": len(data)},
            "errors": [],
        }

    except Exception as exc:
        return {
            "data": [],
            "meta": {"source": str(JIRA_ISSUES_FILE), "count": 0},
            "errors": [{"source": "jira", "message": "Failed to parse Jira issues file", "reason": str(exc)}],
        }
