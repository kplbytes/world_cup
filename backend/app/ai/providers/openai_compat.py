"""OpenAI-compatible API provider base class."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from app.ai.providers.base import (
    AIPredictionError,
    AIProviderBase,
    AIProviderConfig,
    AIModelConfig,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a football match prediction assistant. "
    "You must ONLY use the data provided in the user message. "
    "Do NOT use any external knowledge about injuries, odds, or news. "
    "If data is missing, mark it as 'unknown'. "
    "Return ONLY a JSON object, no other text. "
    "Do NOT output betting advice. "
    "Probabilities must sum to 1.0. "
    "You must explicitly state uncertainties."
)


class OpenAICompatProvider(AIProviderBase):
    """Base class for OpenAI-compatible API providers."""

    default_base_url: str = "https://api.openai.com/v1"
    provider_name: str = "openai_compat"

    def __init__(self, provider_config: AIProviderConfig) -> None:
        super().__init__(provider_config)
        self._client: httpx.AsyncClient | None = None
        self._clients_by_loop: dict[asyncio.AbstractEventLoop | None, httpx.AsyncClient] = {}

    def _get_client(self, timeout: float) -> httpx.AsyncClient:
        """Get or create an AsyncClient for the current event loop.

        Reusing one AsyncClient across different asyncio event loops can raise
        "bound to a different event loop" errors in long-lived local apps where
        FastAPI requests, background jobs, and direct asyncio.run() calls mix.
        """
        try:
            loop_key = asyncio.get_running_loop()
        except RuntimeError:
            loop_key = None

        client = self._clients_by_loop.get(loop_key)
        if client is None or client.is_closed:
            client = httpx.AsyncClient(timeout=timeout)
            self._clients_by_loop[loop_key] = client
        self._client = client
        return client

    async def close(self) -> None:
        """Close the HTTP client."""
        clients = {id(client): client for client in self._clients_by_loop.values()}
        if self._client is not None:
            clients[id(self._client)] = self._client
        for client in clients.values():
            if not client.is_closed:
                await client.aclose()
        self._clients_by_loop.clear()
        self._client = None

    async def predict(
        self,
        model_config: AIModelConfig,
        prompt: str,
        temperature: float | None = None,
        timeout: float | None = None,
    ) -> tuple[str | None, AIPredictionError | None, int]:
        """Call OpenAI-compatible API and return raw response text."""
        api_key = self.get_api_key()
        if not api_key:
            return None, AIPredictionError(
                error_code="no_api_key",
                error_message=f"API key not set ({self.config.api_key_env})",
                is_retryable=False,
            ), 0

        base_url = self.get_base_url() or self.default_base_url
        url = f"{base_url.rstrip('/')}/chat/completions"

        temp = temperature if temperature is not None else self.config.default_temperature
        tout = timeout if timeout is not None else self.config.default_timeout_seconds

        payload = {
            "model": model_config.model_id,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": temp,
            "response_format": {"type": "json_object"},
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        last_error: AIPredictionError | None = None
        start_time = time.monotonic()

        for attempt in range(self.config.max_retries + 1):
            try:
                client = self._get_client(tout)
                response = await client.post(url, json=payload, headers=headers)

                latency_ms = int((time.monotonic() - start_time) * 1000)

                if response.status_code == 200:
                    data = response.json()
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    usage = data.get("usage", {})
                    logger.info(
                        f"{self.provider_name} {model_config.model_id}: "
                        f"tokens={usage.get('total_tokens', '?')}, "
                        f"latency={latency_ms}ms"
                    )
                    return content, None, latency_ms

                elif response.status_code == 429:
                    try:
                        retry_after = int(response.headers.get("retry-after", 60))
                    except (TypeError, ValueError):
                        retry_after = 60
                    logger.warning(f"Rate limited, retry after {retry_after}s")
                    last_error = AIPredictionError(
                        error_code="rate_limited",
                        error_message=f"Rate limited, retry after {retry_after}s",
                        is_retryable=True,
                    )
                    if attempt < self.config.max_retries:
                        await asyncio.sleep(min(retry_after, 10))

                elif response.status_code >= 500:
                    logger.warning(f"Server error {response.status_code}")
                    last_error = AIPredictionError(
                        error_code="server_error",
                        error_message=f"Server error: {response.status_code}",
                        is_retryable=True,
                    )
                    if attempt < self.config.max_retries:
                        backoff = min(2 ** attempt, 10)
                        await asyncio.sleep(backoff)

                else:
                    error_text = response.text[:500]
                    latency_ms = int((time.monotonic() - start_time) * 1000)
                    return None, AIPredictionError(
                        error_code=f"http_{response.status_code}",
                        error_message=error_text,
                        is_retryable=False,
                    ), latency_ms

            except httpx.TimeoutException:
                logger.warning(f"Timeout on attempt {attempt + 1}")
                last_error = AIPredictionError(
                    error_code="timeout",
                    error_message=f"Request timed out after {tout}s",
                    is_retryable=True,
                )
                if attempt < self.config.max_retries:
                    backoff = min(2 ** attempt, 10)
                    await asyncio.sleep(backoff)

            except httpx.ConnectError as e:
                logger.warning(f"Connection error: {e}")
                last_error = AIPredictionError(
                    error_code="connection_error",
                    error_message=str(e),
                    is_retryable=True,
                )
                if attempt < self.config.max_retries:
                    backoff = min(2 ** attempt, 10)
                    await asyncio.sleep(backoff)

            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                # Drop the client so the next call gets a fresh loop-bound one.
                await self.close()
                latency_ms = int((time.monotonic() - start_time) * 1000)
                return None, AIPredictionError(
                    error_code="unexpected_error",
                    error_message=str(e),
                    is_retryable=False,
                ), latency_ms

        latency_ms = int((time.monotonic() - start_time) * 1000)
        return None, last_error, latency_ms
