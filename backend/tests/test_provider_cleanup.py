"""Tests for AI provider registry and cleanup."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ai.providers.base import AIProviderConfig


@pytest.fixture(autouse=True)
def _clear_registry():
    """Clear the provider registry before and after each test."""
    from app.ai import provider_registry
    provider_registry._providers.clear()
    yield
    provider_registry._providers.clear()


def _make_config(provider_name: str = "deepseek") -> AIProviderConfig:
    """Create a test provider config."""
    return AIProviderConfig(
        provider_name=provider_name,
        enabled=True,
        api_key_env=f"{provider_name.upper()}_API_KEY",
        base_url_env=f"{provider_name.upper()}_BASE_URL",
        default_timeout_seconds=30,
        default_temperature=0.0,
        max_retries=2,
    )


def test_get_provider_returns_singleton():
    """get_provider() should return the same instance on repeated calls."""
    from app.ai.provider_registry import get_provider

    config = _make_config("deepseek")

    with patch("app.ai.provider_registry.get_provider_config", return_value=config):
        provider1 = get_provider("deepseek")
        provider2 = get_provider("deepseek")

    assert provider1 is provider2


def test_get_provider_returns_none_for_unknown():
    """get_provider() should return None for unknown providers."""
    from app.ai.provider_registry import get_provider

    with patch("app.ai.provider_registry.get_provider_config", return_value=None):
        result = get_provider("unknown_provider")

    assert result is None


@pytest.mark.asyncio
async def test_close_all_ai_providers_calls_close():
    """close_all_ai_providers() should call close() on each provider."""
    from app.ai.provider_registry import close_all_ai_providers, _providers
    from app.ai.providers.openai_compat import OpenAICompatProvider

    mock_provider = MagicMock(spec=OpenAICompatProvider)
    mock_provider.close = AsyncMock()

    _providers["deepseek"] = mock_provider

    await close_all_ai_providers()

    mock_provider.close.assert_awaited_once()
    assert len(_providers) == 0


@pytest.mark.asyncio
async def test_close_all_ai_providers_clears_registry():
    """close_all_ai_providers() should clear the registry after closing."""
    from app.ai.provider_registry import close_all_ai_providers, _providers

    mock_provider = MagicMock()
    mock_provider.close = AsyncMock()

    _providers["test"] = mock_provider

    await close_all_ai_providers()

    assert len(_providers) == 0


@pytest.mark.asyncio
async def test_close_all_skips_non_openai_compat():
    """close_all_ai_providers() should skip providers that aren't OpenAICompatProvider."""
    from app.ai.provider_registry import close_all_ai_providers, _providers
    from app.ai.providers.base import AIProviderBase

    # Create a mock that is NOT an OpenAICompatProvider
    mock_provider = MagicMock(spec=AIProviderBase)
    _providers["other"] = mock_provider

    await close_all_ai_providers()

    # Registry should be cleared even though close wasn't called
    assert len(_providers) == 0
