from .jira_project_routes import router as jira_project_router
from .viewer_routes import router as viewer_router
from .diag_routes import router as diag_router
from .agent_routes import router as agent_router
from backend.routes.test_plans_routes import router as test_plans_router


routers = [
    diag_router,
    agent_router,
    jira_project_router,
    viewer_router,
    test_plans_router,

]
