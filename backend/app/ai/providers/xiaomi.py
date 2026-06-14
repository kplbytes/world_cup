"""Xiaomi MiMo AI provider implementation (OpenAI-compatible API)."""

from __future__ import annotations

from app.ai.providers.openai_compat import OpenAICompatProvider


class XiaomiProvider(OpenAICompatProvider):
    """Xiaomi MiMo API provider for match predictions."""

    default_base_url = "https://api.xiaomimimo.com/v1"
    provider_name = "xiaomi"
