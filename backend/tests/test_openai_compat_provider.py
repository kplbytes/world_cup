"""Tests for OpenAI-compatible base provider and its subclasses."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.ai.providers.base import AIModelConfig, AIPredictionError, AIProviderConfig
from app.ai.providers.deepseek import DeepSeekProvider
from app.ai.providers.openai_compat import OpenAICompatProvider
from app.ai.providers.xiaomi import XiaomiProvider
from app.config import settings as _settings


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_provider_config(**overrides) -> AIProviderConfig:
    defaults = dict(
        provider_name="test",
        enabled=True,
        api_key_env="DEEPSEEK_API_KEY",
        base_url_env="DEEPSEEK_BASE_URL",
        default_timeout_seconds=30,
        default_temperature=0.3,
        max_retries=2,
    )
    defaults.update(overrides)
    return AIProviderConfig(**defaults)


def _make_model_config() -> AIModelConfig:
    return AIModelConfig(
        provider_name="test",
        model_id="test-model",
        enabled=True,
        model_version="v1",
        display_name="Test Model",
        cost_tier="low",
        latency_tier="fast",
        role="fast_baseline",
    )


# ---------------------------------------------------------------------------
# 1-6: Inheritance and class attributes
# ---------------------------------------------------------------------------

class TestInheritance:
    def test_deepseek_inherits_openai_compat(self):
        assert issubclass(DeepSeekProvider, OpenAICompatProvider)

    def test_xiaomi_inherits_openai_compat(self):
        assert issubclass(XiaomiProvider, OpenAICompatProvider)

    def test_deepseek_default_base_url(self):
        assert DeepSeekProvider.default_base_url == "https://api.deepseek.com"

    def test_xiaomi_default_base_url(self):
        assert XiaomiProvider.default_base_url == "https://api.xiaomimimo.com/v1"

    def test_deepseek_provider_name(self):
        assert DeepSeekProvider.provider_name == "deepseek"

    def test_xiaomi_provider_name(self):
        assert XiaomiProvider.provider_name == "xiaomi"


# ---------------------------------------------------------------------------
# 7-8: Client reuse and close
# ---------------------------------------------------------------------------

class TestClientLifecycle:
    def test_get_client_returns_same_instance(self):
        provider = OpenAICompatProvider(_make_provider_config())
        client1 = provider._get_client(30)
        client2 = provider._get_client(30)
        assert client1 is client2

    def test_get_client_recreates_for_different_event_loop(self):
        provider = OpenAICompatProvider(_make_provider_config())

        async def _get_client():
            return provider._get_client(30)

        client1 = asyncio.run(_get_client())
        client2 = asyncio.run(_get_client())

        assert client1 is not client2
        asyncio.run(client2.aclose())

    def test_close_closes_client(self):
        async def _run():
            provider = OpenAICompatProvider(_make_provider_config())
            client = provider._get_client(30)
            assert not client.is_closed
            await provider.close()
            assert client.is_closed
            assert provider._client is None

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# 9: 429 with non-integer retry-after
# ---------------------------------------------------------------------------

class TestRateLimitNonIntegerRetryAfter:
    def test_429_non_integer_retry_after_no_crash(self):
        async def _run():
            provider = DeepSeekProvider(_make_provider_config(max_retries=1))

            mock_response = MagicMock(spec=httpx.Response)
            mock_response.status_code = 429
            mock_response.headers = {"retry-after": "not-a-number"}
            mock_response.json.return_value = {}

            mock_client = AsyncMock(spec=httpx.AsyncClient)
            mock_client.post.return_value = mock_response
            mock_client.is_closed = False

            with patch.object(_settings, 'deepseek_api_key', 'fake-key'), \
                 patch.object(_settings, 'deepseek_base_url', ''), \
                 patch.object(provider, "_get_client", return_value=mock_client), \
                 patch("app.ai.providers.openai_compat.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                result, error, latency = await provider.predict(_make_model_config(), "test prompt")
                # Should not crash, should use fallback 60s (capped to 10)
                mock_sleep.assert_awaited_once_with(10)
                assert error is not None
                assert error.error_code == "rate_limited"

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# 10: 5xx triggers backoff sleep
# ---------------------------------------------------------------------------

class TestServerErrorBackoff:
    def test_5xx_triggers_backoff(self):
        async def _run():
            provider = DeepSeekProvider(_make_provider_config(max_retries=1))

            mock_response = MagicMock(spec=httpx.Response)
            mock_response.status_code = 500
            mock_response.headers = {}

            mock_client = AsyncMock(spec=httpx.AsyncClient)
            mock_client.post.return_value = mock_response
            mock_client.is_closed = False

            with patch.object(_settings, 'deepseek_api_key', 'fake-key'), \
                 patch.object(_settings, 'deepseek_base_url', ''), \
                 patch.object(provider, "_get_client", return_value=mock_client), \
                 patch("app.ai.providers.openai_compat.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                result, error, latency = await provider.predict(_make_model_config(), "test prompt")
                # attempt 0 → backoff = min(2^0, 10) = 1
                mock_sleep.assert_awaited_once_with(1)
                assert error is not None
                assert error.error_code == "server_error"

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# 11: Timeout triggers backoff sleep
# ---------------------------------------------------------------------------

class TestTimeoutBackoff:
    def test_timeout_triggers_backoff(self):
        async def _run():
            provider = DeepSeekProvider(_make_provider_config(max_retries=1))

            mock_client = AsyncMock(spec=httpx.AsyncClient)
            mock_client.post.side_effect = httpx.TimeoutException("timed out")
            mock_client.is_closed = False

            with patch.object(_settings, 'deepseek_api_key', 'fake-key'), \
                 patch.object(_settings, 'deepseek_base_url', ''), \
                 patch.object(provider, "_get_client", return_value=mock_client), \
                 patch("app.ai.providers.openai_compat.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                result, error, latency = await provider.predict(_make_model_config(), "test prompt")
                mock_sleep.assert_awaited_once_with(1)
                assert error is not None
                assert error.error_code == "timeout"

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# 12: Connect error triggers backoff sleep
# ---------------------------------------------------------------------------

class TestConnectErrorBackoff:
    def test_connect_error_triggers_backoff(self):
        async def _run():
            provider = DeepSeekProvider(_make_provider_config(max_retries=1))

            mock_client = AsyncMock(spec=httpx.AsyncClient)
            mock_client.post.side_effect = httpx.ConnectError("connection refused")
            mock_client.is_closed = False

            with patch.object(_settings, 'deepseek_api_key', 'fake-key'), \
                 patch.object(_settings, 'deepseek_base_url', ''), \
                 patch.object(provider, "_get_client", return_value=mock_client), \
                 patch("app.ai.providers.openai_compat.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                result, error, latency = await provider.predict(_make_model_config(), "test prompt")
                mock_sleep.assert_awaited_once_with(1)
                assert error is not None
                assert error.error_code == "connection_error"

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# 13: No API key error
# ---------------------------------------------------------------------------

class TestNoApiKey:
    def test_no_api_key_returns_error(self):
        async def _run():
            provider = DeepSeekProvider(_make_provider_config(api_key_env="NONEXISTENT_KEY_12345"))

            result, error, latency = await provider.predict(_make_model_config(), "test prompt")

            assert result is None
            assert error is not None
            assert error.error_code == "no_api_key"
            assert error.is_retryable is False
            assert latency == 0

        asyncio.run(_run())
