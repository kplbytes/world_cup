# World Cup Predictor - Project Memory

## Tech Stack
- Backend: FastAPI + SQLAlchemy + SQLite + NumPy/SciPy
- Frontend: React 19 + TypeScript + Vite + React Query (no router, useState-based)
- Prediction: Elo + Poisson + Monte Carlo qualification simulation
- AI: DeepSeek V4 Flash/Pro via configurable AI Model Registry

## Model Versions
- `elo-poisson-v1` - Baseline model
- `elo-poisson-v1-intel-numeric` - With injury/suspension numerical adjustments
- 8 additional config-driven versions (draw_boost, favorite_dampened, upset, market_lite, etc.)
- `ai-deepseek-v4-flash-v1` - DeepSeek V4 Flash AI prediction
- `ai-deepseek-v4-pro-v1` - DeepSeek V4 Pro AI prediction
- `ensemble-v1` - System + Market + AI multi-source fusion

## Key Architecture Decisions
- PredictionSnapshot locked at T-30, fallback lock for missed windows
- Only locked snapshots participate in scoring (no data leakage)
- AI predictions also follow T-30 lock; post-T-30 AI = real_time_only, not scored
- Model config in `app/model_configs/model_configs.yaml` with YAML-based parameter sets
- AI model config in `app/ai/ai_models.yaml` with provider/model registry
- Error attribution: 13 types covering all major prediction failure modes
- Market comparison: automatic blend weight search via grid search
- Ensemble: weighted fusion of system (50%) + market (20%) + AI (30%) with auto-degradation
- Tournament: full 48-team World Cup cycle (group -> R32 -> R16 -> QF -> SF -> Final)
- Match model supports nullable team IDs for knockout placeholder matches
- AI is opt-in: ENABLE_AI_PREDICTION=false by default, no API key required for system to work
- Routes split into sub-modules: dashboard_routes, scoring_routes, ai_routes, tournament_routes, data_routes

## Key Modules (P2+ Hardening)
- `app/ai/` - AI model registry, providers, prompt builder, parser, service, ensemble, evaluation
- `app/tournament/` - standings, qualification, bracket, simulation, rules
- `app/services/accuracy_command.py` - Unified accuracy command center
- `app/services/recompute.py` - Split into compute_standings, compute_match_predictions, compute_group_qualification
- `app/api/routes/` - Split into 5 sub-modules
- `app/models.py` - AIPrediction, EnsemblePrediction tables; Match extended with stage/bracket fields
- Frontend: BracketView, TournamentProjectionView, AIModelComparisonView, AccuracyCommandCenterView, AccuracyPanel

## Data Status (as of 2026-06-13)
- 4 completed matches, all fallback-locked (no T-30 lock captured)
- Hit rate: 0%, Brier: 0.5334
- Sample too small for reliable model switching
- Continue with baseline (elo-poisson-v1) until more matches completed
- AI predictions enabled but no scored data yet
- DeepSeek API Key configured in .env

## Important Paths
- Config YAML: `backend/app/model_configs/model_configs.yaml`
- AI Config: `backend/app/ai/ai_models.yaml`
- Artifacts: `artifacts/` (experiments, calibration, reports, AI evaluation, hardening)
- Scripts: `backend/scripts/`
- Routes: `backend/app/api/routes/` (dashboard, scoring, ai, tournament, data)

## API Endpoints (36 total)
- Dashboard: health, dashboard, groups, matches, teams, data-sources, sync-runs, refresh, decision
- Scoring: model-score, model-score/details, model-score/by-version, model-score/by-stage, model-calibration, market-comparison, model-recommendation, data-quality, model-configs
- AI: ai-models, ai-predictions/run, ai-predictions/run-match, ai-predictions/run-all, ai-predictions, ensemble/run, ensemble, ai-evaluation
- Tournament: tournament/bracket, tournament/projections, tournament/simulate, tournament/team-path, tournament/standings
- Data: manual-adjustments (GET/POST/DELETE), accuracy-command-center

## Bug Fixes Applied (P2+ Hardening)
- service.py: group_code `in "ABCDEFGHIJKL"` → `in list("ABCDEFGHIJKL")` (fragile substring match)
- service.py: hardcoded default probabilities removed — now refuses AI call without system prediction
- service.py: _get_provider() → dynamic class loading via _PROVIDERS registry
- model_registry.py: get_ensemble_defaults() now uses cached data instead of re-reading YAML
- model_registry.py: reload() now properly clears _registry, _models, _ensemble_defaults
- ensemble.py: AI prediction query now filters real_time_only=False
- scoring_routes.py: model-score/by-stage now uses real stage-filtered scoring function
- AIModelConfig: added prompt_version field
- ai_models.yaml: added prompt_version to both models
- list_ai_model_status: added disabled_no_key, has_api_key, provider_health, last_error fields
