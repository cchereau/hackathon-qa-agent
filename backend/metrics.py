# backend/metrics.py
from prometheus_client import Counter, Histogram, CollectorRegistry

# Registry dédié pour éviter les conflits (reload, imports multiples)
REGISTRY = CollectorRegistry(auto_describe=True)

LLM_REQUESTS = Counter(
    "llm_requests_total",
    "Number of LLM requests",
    ["outcome"],
    registry=REGISTRY,
)

LLM_LATENCY = Histogram(
    "llm_latency_seconds",
    "Latency of LLM requests in seconds",
    registry=REGISTRY,
)
