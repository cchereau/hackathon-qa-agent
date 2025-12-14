# backend/main.py
import logging
import traceback

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app

from backend.errors import LLMConnectionError
from backend.metrics import REGISTRY
from backend.metrics import LLM_REQUESTS, LLM_LATENCY  # side-effects only
from backend.routes import routers

# ----------------------------------------------------------------------
# Logger configuration
# ----------------------------------------------------------------------
logger = logging.getLogger("qa-test-plan-agent")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
handler.setFormatter(formatter)

if not logger.handlers:
    logger.addHandler(handler)
else:
    for h in logger.handlers:
        h.setFormatter(formatter)

# ----------------------------------------------------------------------
# FastAPI app + CORS
# ----------------------------------------------------------------------
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------------------------------------------------
# Routers (single source of truth: backend/routes/__init__.py)
# ----------------------------------------------------------------------
for r in routers:
    app.include_router(r)

# ----------------------------------------------------------------------
# Metrics
# ----------------------------------------------------------------------
app.mount("/metrics", make_asgi_app(registry=REGISTRY))

# ----------------------------------------------------------------------
# Exception handlers
# ----------------------------------------------------------------------
@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error(
        f"Unhandled exception on {request.url.path}: {exc}\n{traceback.format_exc()}"
    )
    detail = str(exc) if app.debug else "Internal server error"
    return JSONResponse(
        status_code=500,
        content={"error": "internal_server_error", "detail": detail},
    )


@app.exception_handler(LLMConnectionError)
async def llm_error_handler(request: Request, exc: LLMConnectionError):
    logger.error(f"LLM error on {request.url.path}: {exc}")
    return JSONResponse(
        status_code=502,
        content={"error": "llm_unavailable", "detail": str(exc)},
    )
