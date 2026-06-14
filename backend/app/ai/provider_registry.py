"""AI Provider registry for managing provider instances."""

from __future__ import annotations

import logging
from typing import Dict

from app.ai.providers.base import AIProviderBase, AIProviderConfig
from app.ai.model_registry import get_provider_config, list_enabled_providers

logger = logging.getLogger(__name__)

_providers: Dict[str, AIProviderBase] = {}


def get_provider(provider_name: str) -> AIProviderBase | None:
    """Get or create a provider instance."""
    if provider_name in _providers:
        return _providers[provider_name]

    provider_config = get_provider_config(provider_name)
    if not provider_config:
        return None

    # Map provider names to their implementation classes
    _PROVIDER_CLASSES = {
        "deepseek": "app.ai.providers.deepseek.DeepSeekProvider",
        "xiaomi": "app.ai.providers.xiaomi.XiaomiProvider",
    }

    class_path = _PROVIDER_CLASSES.get(provider_name)
    if not class_path:
        return None

    module_path, class_name = class_path.rsplit(".", 1)
    import importlib
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)

    provider = cls(provider_config)
    _providers[provider_name] = provider
    return provider


async def close_all_ai_providers() -> None:
    """Close all provider HTTP clients."""
    from app.ai.providers.openai_compat import OpenAICompatProvider
    for name, provider in _providers.items():
        if isinstance(provider, OpenAICompatProvider):
            await provider.close()
            logger.info(f"Closed AI provider: {name}")
    _providers.clear()
