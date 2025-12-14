# backend/routes/__init__.py
"""Router registry.

Single source of truth for FastAPI route inclusion.

IMPORTANT:
- Keep this list deterministic and explicit.
- Hackathon T0 relies on these routers being mounted once (no duplicates).
"""

from backend.routes.agent_routes import router as agent_router
from backend.routes.diag_routes import router as diag_router
from backend.routes.jira_project_routes import router as jira_project_router
from backend.routes.test_plans_routes import router as test_plans_router
from backend.routes.viewer_routes import router as viewer_router
from backend.routes.junction_routes import router as junction_router

routers = [
    diag_router,
    jira_project_router,
    agent_router,
    viewer_router,
    test_plans_router,
    junction_router,
]

__all__ = ["routers"]
