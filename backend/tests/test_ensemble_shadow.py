"""Test that shadow models are excluded from ensemble."""
import pytest
from unittest.mock import patch, MagicMock


class TestEnsembleExcludesShadow:
    def test_include_in_ensemble_default_true(self):
        """Models without explicit include_in_ensemble default to True."""
        from app.ai.providers.base import AIModelConfig
        config = AIModelConfig(
            provider_name="test", model_id="m1", enabled=True,
            model_version="test-v1", display_name="Test",
            cost_tier="low", latency_tier="fast", role="general",
        )
        assert config.include_in_ensemble is True

    def test_include_in_ensemble_false(self):
        """Shadow models can opt out of ensemble."""
        from app.ai.providers.base import AIModelConfig
        config = AIModelConfig(
            provider_name="test", model_id="m1", enabled=True,
            model_version="test-shadow-v1", display_name="Test Shadow",
            cost_tier="low", latency_tier="fast", role="shadow",
            include_in_ensemble=False,
        )
        assert config.include_in_ensemble is False

    def test_should_include_in_ensemble_with_config(self):
        """_should_include_in_ensemble respects model config."""
        from app.ai.ensemble import _should_include_in_ensemble
        from app.ai.providers.base import AIModelConfig
        from app.ai import model_registry

        # Mock get_model_config
        shadow_config = AIModelConfig(
            provider_name="test", model_id="m1", enabled=True,
            model_version="ai-shadow-v1", display_name="Shadow",
            cost_tier="low", latency_tier="fast", role="shadow",
            include_in_ensemble=False,
        )
        normal_config = AIModelConfig(
            provider_name="test", model_id="m2", enabled=True,
            model_version="ai-normal-v1", display_name="Normal",
            cost_tier="low", latency_tier="fast", role="general",
            include_in_ensemble=True,
        )

        with patch.object(model_registry, 'get_model_config') as mock:
            mock.side_effect = lambda v: {
                "ai-shadow-v1": shadow_config,
                "ai-normal-v1": normal_config,
            }.get(v)
            assert _should_include_in_ensemble("ai-shadow-v1") is False
            assert _should_include_in_ensemble("ai-normal-v1") is True

    def test_should_include_unknown_model(self):
        """Unknown models are included by default (backward compat)."""
        from app.ai.ensemble import _should_include_in_ensemble
        from app.ai import model_registry

        with patch.object(model_registry, 'get_model_config', return_value=None):
            assert _should_include_in_ensemble("unknown-model") is True

    def test_v2_models_have_include_in_ensemble_false(self):
        """All v2 models in ai_models.yaml should have include_in_ensemble=false (shadow)
        or include_in_ensemble=true with role=independent_judge."""
        from app.ai.model_registry import reload, list_enabled_models
        reload()
        models = list_enabled_models()
        v2_models = [m for m in models if m.model_version.endswith("-v2")]
        for m in v2_models:
            if m.include_in_ensemble:
                assert m.role == "independent_judge", f"{m.model_version} has include_in_ensemble=True but role={m.role}, expected independent_judge"
            else:
                assert m.role == "shadow", f"{m.model_version} has include_in_ensemble=False but role={m.role}, expected shadow"
