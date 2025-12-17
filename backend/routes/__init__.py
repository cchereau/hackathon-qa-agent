# backend/routes/__init__.py
"""Router registry.

Single source of truth for FastAPI route inclusion.

Guidelines:
- Keep this list deterministic and explicit.
- Each router must be mounted exactly once (no duplicates).
- Group routers by functional domain to reduce cognitive load.
"""

from __future__ import annotations

from backend.routes.agent_routes import router as agent_router
from backend.routes.diag_routes import router as diag_router
from backend.routes.jira_project_routes import router as jira_project_router
from backend.routes.junction_routes import router as junction_router
from backend.routes.test_plans_effective_routes import router as test_plans_effective_router
from backend.routes.test_plans_routes import router as test_plans_router
from backend.routes.viewer_routes import router as viewer_router

# Deterministic inclusion order:
# 1) Diagnostics / project meta
# 2) Read-only viewers (transparency)
# 3) Business APIs (agent, test-plans, junction)
routers = [
    diag_router,
    jira_project_router,
    viewer_router,
    agent_router,
    # Keep these adjacent (shared prefix="/api/test-plans")
    test_plans_router,
    test_plans_effective_router,
    junction_router,
]

__all__ = ["routers"]
