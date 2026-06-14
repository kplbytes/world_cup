"""DeepSeek AI provider implementation."""

from __future__ import annotations

from app.ai.providers.openai_compat import OpenAICompatProvider


class DeepSeekProvider(OpenAICompatProvider):
    """DeepSeek API provider for match predictions."""

    default_base_url = "https://api.deepseek.com"
    provider_name = "deepseek"
