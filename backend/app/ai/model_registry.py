"""AI Model Registry - loads AI model configurations from ai_models.yaml."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.ai.providers.base import AIModelConfig, AIProviderConfig

_CONFIG_PATH = Path(__file__).resolve().parent / "ai_models.yaml"

_registry: dict[str, AIProviderConfig] = {}
_models: dict[str, AIModelConfig] = {}
_ensemble_defaults: dict[str, float] = {}
_loaded = False


def _ensure_loaded() -> None:
    global _loaded, _registry, _models, _ensemble_defaults
    if _loaded:
        return
    _loaded = True
    _registry = {}
    _models = {}
    _ensemble_defaults = {}
    if not _CONFIG_PATH.exists():
        return
    with open(_CONFIG_PATH) as f:
        data = yaml.safe_load(f) or {}

    _ensemble_defaults = data.get("ensemble_defaults", {})

    for prov_name, prov_data in data.get("providers", {}).items():
        if not prov_data.get("enabled", False):
            continue
        prov_config = AIProviderConfig(
            provider_name=prov_name,
            enabled=prov_data.get("enabled", False),
            api_key_env=prov_data.get("api_key_env", f"{prov_name.upper()}_API_KEY"),
            base_url_env=prov_data.get("base_url_env", f"{prov_name.upper()}_BASE_URL"),
            default_timeout_seconds=prov_data.get("default_timeout_seconds", 30),
            default_temperature=prov_data.get("default_temperature", 0),
            max_retries=prov_data.get("max_retries", 2),
        )
        _registry[prov_name] = prov_config
        for model_data in prov_data.get("models", []):
            if not model_data.get("enabled", False):
                continue
            model_config = AIModelConfig(
                provider_name=prov_name,
                model_id=model_data["model_id"],
                enabled=model_data.get("enabled", False),
                model_version=model_data["model_version"],
                display_name=model_data.get("display_name", model_data["model_id"]),
                cost_tier=model_data.get("cost_tier", "medium"),
                latency_tier=model_data.get("latency_tier", "medium"),
                role=model_data.get("role", "general"),
                ensemble_weight=model_data.get("ensemble_weight", 1.0),
                prompt_version=model_data.get("prompt_version", "worldcup-ai-v1"),
                include_in_ensemble=model_data.get("include_in_ensemble", True),
            )
            _models[model_config.model_version] = model_config


def get_provider_config(provider_name: str) -> AIProviderConfig | None:
    _ensure_loaded()
    return _registry.get(provider_name)


def get_model_config(model_version: str) -> AIModelConfig | None:
    _ensure_loaded()
    return _models.get(model_version)


def list_enabled_models() -> list[AIModelConfig]:
    _ensure_loaded()
    return list(_models.values())


def list_enabled_providers() -> list[AIProviderConfig]:
    _ensure_loaded()
    return list(_registry.values())


def get_ensemble_defaults() -> dict[str, float]:
    _ensure_loaded()
    return dict(_ensemble_defaults)


def reload() -> None:
    global _loaded
    _loaded = False
    _ensure_loaded()
