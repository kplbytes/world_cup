"""Model configuration loader.

Loads model parameter configs from YAML and provides them to the prediction engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


CONFIG_PATH = Path(__file__).resolve().parent / "model_configs.yaml"


@dataclass
class ModelConfig:
    """A single model configuration parameter set."""
    name: str
    description: str = ""
    elo_scale: float = 400.0
    base_goal_mean_home: float = 1.25
    base_goal_mean_away: float = 1.10
    strength_coeff_home: float = 0.90
    strength_coeff_away: float = 0.75
    home_advantage: float = 0.0
    draw_boost: float = 1.00
    favorite_dampening: float = 0.00
    underdog_boost: float = 0.00
    recent_form_weight: float = 0.0
    market_blend_weight: float = 0.00
    numerical_adjustment_weight: float = 1.00
    max_xg: float = 3.50
    min_xg: float = 0.20
    poisson_dispersion: float = 1.00
    upset_factor: float = 0.00
    # Research-enhanced fields
    smart_market_blend: bool = True
    dynamic_draw_boost: bool = True
    # Profile integration fields
    profile_weight: float = 0.0
    profile_adjust_attack_defense: bool = False
    profile_adjust_form: bool = False
    fifa_rank_weight: float = 0.15
    # Dixon-Coles low-score correction parameter.
    # Negative rho boosts 0-0 and 1-1 (negative scoring correlation), which
    # is typical for football where draws are more common than pure Poisson
    # predicts. Default -0.02 is a mild correction.
    dixon_coles_rho: float = -0.02


# Singleton cache
_configs: dict[str, ModelConfig] | None = None


def load_configs(path: Path | None = None) -> dict[str, ModelConfig]:
    """Load all model configs from YAML, resolving _base references."""
    global _configs
    if _configs is not None and path is None:
        return _configs

    yaml_path = path or CONFIG_PATH
    with open(yaml_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    base_params = raw.get("elo_poisson_base", {})
    versions = raw.get("model_versions", {})

    configs: dict[str, ModelConfig] = {}
    for name, params in versions.items():
        # Start with base params, then overlay version-specific params
        merged = dict(base_params)
        for k, v in params.items():
            if k not in ("_base", "description"):
                merged[k] = v

        configs[name] = ModelConfig(
            name=name,
            description=params.get("description", ""),
            elo_scale=float(merged.get("elo_scale", 400.0)),
            base_goal_mean_home=float(merged.get("base_goal_mean_home", 1.25)),
            base_goal_mean_away=float(merged.get("base_goal_mean_away", 1.10)),
            strength_coeff_home=float(merged.get("strength_coeff_home", 0.90)),
            strength_coeff_away=float(merged.get("strength_coeff_away", 0.75)),
            home_advantage=float(merged.get("home_advantage", 0.0)),
            draw_boost=float(merged.get("draw_boost", 1.00)),
            favorite_dampening=float(merged.get("favorite_dampening", 0.00)),
            underdog_boost=float(merged.get('underdog_boost', 0.00)),
            recent_form_weight=float(merged.get("recent_form_weight", 0.0)),
            market_blend_weight=float(merged.get("market_blend_weight", 0.00)),
            numerical_adjustment_weight=float(merged.get("numerical_adjustment_weight", 1.00)),
            max_xg=float(merged.get("max_xg", 3.50)),
            min_xg=float(merged.get("min_xg", 0.20)),
            poisson_dispersion=float(merged.get("poisson_dispersion", 1.00)),
            upset_factor=float(merged.get("upset_factor", 0.00)),
            smart_market_blend=bool(merged.get("smart_market_blend", True)),
            dynamic_draw_boost=bool(merged.get("dynamic_draw_boost", True)),
            profile_weight=float(merged.get("profile_weight", 0.0)),
            profile_adjust_attack_defense=bool(merged.get("profile_adjust_attack_defense", False)),
            profile_adjust_form=bool(merged.get("profile_adjust_form", False)),
            fifa_rank_weight=float(merged.get("fifa_rank_weight", 0.15)),
            dixon_coles_rho=float(merged.get("dixon_coles_rho", -0.02)),
        )

    if path is None:
        _configs = configs
    return configs


def get_config(model_version: str) -> ModelConfig:
    """Get a specific model config by version name."""
    configs = load_configs()
    if model_version not in configs:
        # Return default base config
        return ModelConfig(name=model_version)
    return configs[model_version]


def list_configs() -> list[dict[str, Any]]:
    """List all available model configs as dicts."""
    configs = load_configs()
    return [
        {
            "name": c.name,
            "description": c.description,
            "draw_boost": c.draw_boost,
            "favorite_dampening": c.favorite_dampening,
            "underdog_boost": c.underdog_boost,
            "market_blend_weight": c.market_blend_weight,
            "numerical_adjustment_weight": c.numerical_adjustment_weight,
            "upset_factor": c.upset_factor,
        }
        for c in configs.values()
    ]


def reload_configs() -> dict[str, ModelConfig]:
    """Force reload configs from disk."""
    global _configs
    _configs = None
    return load_configs()
