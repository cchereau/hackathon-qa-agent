# backend/llm_client/llm_client.py
"""
LLM client wrapper (hackathon-ready)

Supports:
- LLM_PROVIDER=mock     -> no network, deterministic output
- LLM_PROVIDER=openai   -> OpenAI Chat Completions (base_url + /chat/completions)
- LLM_PROVIDER=internal -> Internal LLMaaS (base_url + optional path)

Includes:
- httpx async
- tenacity retry
- aiobreaker circuit breaker
- Prometheus metrics (requests + latency)
"""

import json
import logging
from datetime import timedelta
from typing import Any, Dict, List

import httpx
from aiobreaker import CircuitBreaker, CircuitBreakerError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from backend.config import (
    LLM_API_TOKEN,
    LLM_BASE_URL,
    LLM_CHAT_PATH,
    LLM_MODEL,
    LLM_PROVIDER,
    LLM_TIMEOUT_SECONDS,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_CHAT_PATH,
    validate_llm_config,
)
from backend.metrics import LLM_LATENCY, LLM_REQUESTS

logger = logging.getLogger("qa-test-plan-agent.llm_client")
logger.setLevel(logging.INFO)

# Circuit breaker: 5 failures -> open for 30s
breaker = CircuitBreaker(
    fail_max=5,
    timeout_duration=timedelta(seconds=30),
    exclude=(httpx.HTTPStatusError,),
)


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(
        exc,
        (
            httpx.ConnectError,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.RemoteProtocolError,
            httpx.ConnectTimeout,
        ),
    )


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception(_is_retryable),
)
async def _post_with_retry(
    client: httpx.AsyncClient,
    url: str,
    json_payload: Dict[str, Any],
) -> httpx.Response:
    """
    POST wrapper with retry + Prometheus metrics.
    """
    with LLM_LATENCY.time():
        try:
            resp = await client.post(url, json=json_payload)
            resp.raise_for_status()
            LLM_REQUESTS.labels(outcome="success").inc()
            return resp
        except Exception:
            LLM_REQUESTS.labels(outcome="failure").inc()
            raise


class LLMClient:
    """
    Unified LLM client for OpenAI / Internal / Mock.

    IMPORTANT:
    - base_url is always a BASE (e.g. https://api.openai.com/v1)
    - chat_path is always a PATH (e.g. /chat/completions)
    """

    def __init__(self) -> None:
        validate_llm_config()

        self.provider = LLM_PROVIDER
        self.model = LLM_MODEL
        self.timeout = httpx.Timeout(float(LLM_TIMEOUT_SECONDS))

        # Defaults
        self.base_url = ""
        self.chat_path = ""
        self.headers: Dict[str, str] = {}

        if self.provider == "openai":
            # OpenAI expects base_url + /chat/completions
            self.base_url = OPENAI_BASE_URL.rstrip("/")
            self.chat_path = OPENAI_CHAT_PATH or "/chat/completions"
            if not self.chat_path.startswith("/"):
                self.chat_path = f"/{self.chat_path}"

            self.headers = {
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }

        elif self.provider == "internal":
            self.base_url = LLM_BASE_URL.rstrip("/")
            # If LLM_CHAT_PATH is empty, we assume LLM_BASE_URL is already the full endpoint.
            self.chat_path = (LLM_CHAT_PATH or "").strip()
            if self.chat_path and not self.chat_path.startswith("/"):
                self.chat_path = f"/{self.chat_path}"

            # Some internal gateways require a bearer token, others might not.
            self.headers = {
                "Authorization": f"Bearer {LLM_API_TOKEN}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }

        else:
            # mock: no network
            pass

    async def chat(self, messages: List[Dict[str, str]]) -> str:
        """
        Chat completion: returns the assistant content as a string.
        """
        if self.provider == "mock":
            # Prometheus: still track that the feature was used
            LLM_REQUESTS.labels(outcome="mock").inc()
            user = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
            return (
                "## Mock Test Plan\n"
                "- Objective: Demonstrate prompt impact\n"
                "- Scope: Based on provided Jira + existing tests + code changes\n\n"
                f"### Extract (first 200 chars)\n{user[:200]}\n"
            )

        payload = {"model": self.model, "messages": messages}

        # endpoint selection:
        # - openai: chat_path="/chat/completions"
        # - internal: chat_path may be "" (base_url is full endpoint) or "/chat/completions"
        endpoint = self.chat_path  # can be ""

        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers=self.headers,
            timeout=self.timeout,
        ) as client:
            logger.debug(
                f"LLM[{self.provider}] → POST {self.base_url}{endpoint} | payload={json.dumps(payload)[:500]}"
            )

            try:
                resp = await breaker.call(_post_with_retry, client, endpoint, payload)
            except CircuitBreakerError:
                logger.warning("LLM circuit breaker OPEN – request blocked")
                LLM_REQUESTS.labels(outcome="circuit_breaker").inc()
                raise RuntimeError("LLM service temporarily unavailable (circuit breaker open).")
            except httpx.HTTPStatusError as exc:
                logger.error(f"LLM HTTP error {exc.response.status_code}: {exc.response.text}")
                raise RuntimeError(f"LLM HTTP error {exc.response.status_code}: {exc.response.text}")
            except Exception as exc:
                logger.error(f"LLM request failed: {exc}")
                raise RuntimeError(f"LLM request failed: {exc}")

            logger.debug(f"LLM[{self.provider}] ← {resp.status_code} | response={resp.text[:500]}")

            # Default parsing (OpenAI-compatible chat completions)
            try:
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            except (KeyError, json.JSONDecodeError) as exc:
                logger.error(f"Malformed LLM response: {exc} – raw={resp.text}")
                raise RuntimeError("LLM returned malformed response.")
