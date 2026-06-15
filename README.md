# 2026 FIFA World Cup Prediction Workbench

A local-first, multi-layer prediction system for the 2026 FIFA World Cup. Covers 48 teams, 12 groups, 72 group-stage matches, with expanding knockout-stage paths, pre-match locking, post-match review, AI fusion, and team profiling.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Frontend (React + Vite)               │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐  │
│  │  Daily    │ │  Match   │ │  Model   │ │ Tournament│  │
│  │ Dashboard │ │  Center  │ │  Review  │ │ & Schedule│  │
│  └──────────┘ └──────────┘ └──────────┘ └───────────┘  │
└────────────────────────┬────────────────────────────────┘
                         │ REST API
┌────────────────────────▼────────────────────────────────┐
│                   Backend (FastAPI + SQLite)             │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐  │
│  │  Elo +   │ │   AI     │ │ Ensemble │ │  Team     │  │
│  │  Poisson │ │ Models   │ │          │ │  Profile  │  │
│  └──────────┘ └──────────┘ └──────────┘ └───────────┘  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐  │
│  │  Market  │ │ Scoring  │ │ Snapshot │ │ Workflow  │  │
│  │  Odds    │ │ Engine   │ │  Locking │ │  Engine   │  │
│  └──────────┘ └──────────┘ └──────────┘ └───────────┘  │
└─────────────────────────────────────────────────────────┘
```

## Prediction Pipeline

The system follows a strict prediction pipeline to ensure pre-match integrity:

```
Data Sync → Baseline (Elo+Poisson) → AI Predictions → Ensemble → 24h Lock → Kickoff → Scoring
```

1. **Baseline**: `elo-poisson-v1` generates win/draw/loss probabilities, xG, and scorelines
2. **AI**: Multiple AI models (DeepSeek V4 Flash/Pro, Xiaomi MiMo) produce independent predictions
3. **Ensemble**: Weighted fusion of baseline + market + AI predictions
4. **24h Lock**: Pre-match snapshots locked within 24h of kickoff, frozen at kickoff
5. **Scoring**: Post-match Brier score, log loss, hit rate evaluation against locked snapshots

## Key Features

### Unified Recommendation Logic

Every match card and detail drawer uses the same `getMatchRecommendation()` function with consistent priority:

1. **Ensemble** (if valid) → display Ensemble recommendation
2. **AI** (if valid, no Ensemble) → display AI recommendation
3. **Baseline** (if no AI) → display Baseline recommendation
4. **None** → display "pending generation"

This ensures card and drawer always show the same recommendation direction.

### Live Match Support

- Real-time match status (`live`) synced from football-data.org
- In-progress matches displayed with current scores
- Frontend includes recently-started matches (within 3 hours) in upcoming lists

### Pre-Match Decision Loop

- **24h lock window**: Snapshots generated when match is within 24h of kickoff
- **Frozen at kickoff**: No post-match data can overwrite pre-match predictions
- **Fallback snapshots**: Graceful degradation when locked snapshot is unavailable
- **Decision status**: Clear visibility into lock/fallback/scoring eligibility

### Post-Match Review

- Brier score, log loss, hit rate per model version
- Error attribution (direction miss, calibration drift, market divergence)
- Model comparison and recommendation with sample-size warnings
- Scoring exclusion reasons for matches without pre-match snapshots

### AI & Ensemble

- Multi-model AI: DeepSeek V4 Flash/Pro (v1 + v2 prompts), Xiaomi MiMo V2/V2.5 Pro
- **v2 prompt**: Independent judgment without baseline probability leakage
- Ensemble: Weighted combination of baseline + market + AI
- Deduplication: Skips models that already have predictions (unless forced)
- Identical-to-baseline detection: Flags AI outputs that copy system predictions

### Team Profiles

- Independent model version: `elo-poisson-v1-team-profile`
- Profile-as-of time slicing for historical accuracy
- Risk flags and triggered traits per match
- Pre-match lock support

## Frontend

Four main entry points:

| Page | Purpose |
|------|---------|
| **Daily Dashboard** | Today's status, last night's review, upcoming matches, workflow actions |
| **Match Center** | All matches by group, today, or knockout stage |
| **Model Review** | Model comparison, AI evaluation, error attribution, calibration |
| **Tournament** | Bracket, projections, team paths, standings |

Match details are shown in a shared `MatchDetailDrawer` with tabs for predictions, profiles, risk, and lock status.

## Backend Structure

```
backend/app/
├── api/routes/          # FastAPI endpoints
├── services/
│   ├── dashboard.py     # Dashboard & match detail assembly
│   ├── refresh.py       # Match result sync & recompute trigger
│   ├── recompute.py     # Full recompute, revision, profile candidate
│   ├── scoring.py       # Post-match scoring, exclusions, details
│   ├── snapshots.py     # 24h lock & fallback logic
│   └── accuracy_command.py  # Accuracy command center
├── ai/                  # AI providers, prompts, parsers, ensemble, evaluation
├── team_profiles/       # Data loading, feature engineering, profile service
├── workflows/           # Automated workflow status & execution
├── tournament/          # Standings, bracket, simulation
├── logging_config.py    # Structured JSON logging with rotation
└── middleware.py         # Request ID tracing & access logs
```

## Quick Start

### Prerequisites

- Python 3.12+
- Node.js 20+

### Installation

```bash
./scripts/setup.sh
```

### Daily Usage

```bash
./start.sh    # Start backend + frontend
./stop.sh     # Stop all services
```

Access:
- Frontend: http://127.0.0.1:5173
- Backend: http://127.0.0.1:8000
- API Docs: http://127.0.0.1:8000/docs

### Development Mode

```bash
./scripts/dev.sh   # Hot-reload backend + frontend
```

## Configuration

Copy `.env.example` to `.env` and configure:

```env
# Database
DATABASE_PATH=backend/data/world-cup.sqlite3

# Simulation
SIMULATION_ITERATIONS=50000
SIMULATION_SEED=20260613

# AI (leave empty to disable)
DEEPSEEK_API_KEY=
XIAOMI_API_KEY=
ENABLE_AI_PREDICTION=true
AI_RUN_MODE=manual

# Workflow
AUTO_RUN_DAILY_WORKFLOW_ON_OPEN=true
WORKFLOW_AUTO_RUN_COOLDOWN_MINUTES=60
```

## Data Sources

| Source | Purpose |
|--------|---------|
| OpenFootball | Primary schedule & results |
| football-data.org | Supplementary results + live status |
| Sporttery (China) | Market odds comparison |
| World Football Elo Ratings | Initial Elo ratings |
| `data/seed/` | Local seed & replay data |

When upstream sources are unavailable, the system retains the last successful data.

## Business Rules

### Time & Display

- All storage, comparison, locking, and scoring use UTC
- All user-facing display uses Beijing Time (UTC+8)
- "Today" / "yesterday" / "tomorrow" follow `Asia/Shanghai` calendar

### Pre-Match Lock Priority

Pre-match predictions take absolute priority. Post-match data must never overwrite pre-match decision samples.

24h lock rules:
1. Locked snapshots generated when match is within 24h of kickoff
2. Before kickoff: locked snapshot updated in-place with latest predictions
3. At/after kickoff: locked snapshot frozen permanently
4. Beyond 24h: no locked snapshot generated

### Scoring Sample Criteria

Scoring must distinguish:
- Total finished matches
- Matches with pre-match predictions
- Matches with pre-kickoff snapshots
- Matches with locked/fallback snapshots
- Matches actually entering scoring

"Finished matches" must never be equated with "scoring samples".

## API Overview

| Group | Key Endpoints |
|-------|--------------|
| Dashboard | `/api/dashboard`, `/api/matches/{id}`, `/api/refresh` |
| Scoring | `/api/model-score`, `/api/accuracy-command-center`, `/api/scoring-exclusions` |
| AI | `/api/ai-models`, `/api/ai-predictions`, `/api/ensemble`, `/api/ai-prompt-preview` |
| Workflow | `/api/workflows/status`, `/api/workflows/daily-open`, `/api/workflows/full` |
| Profile | `/api/team-profiles`, `/api/team-profile-predictions/{match_id}` |
| Tournament | `/api/tournament/bracket`, `/api/tournament/projections`, `/api/tournament/standings` |

Full API documentation: http://127.0.0.1:8000/docs

## Testing

```bash
# Backend
cd backend && .venv/bin/python -m pytest tests/ -q

# Frontend
cd frontend && npm test -- --run && npm run typecheck && npm run build
```

## Logging

Structured JSON logs in `data/logs/`:

```bash
# Errors only
cat data/logs/error.jsonl | python3 -m json.tool

# Trace by request ID
grep "REQUEST_ID" data/logs/app.jsonl | python3 -m json.tool

# Slow requests (>1s)
grep "duration_ms" data/logs/app.jsonl | python3 -c "
import sys, json
for line in sys.stdin:
    d = json.loads(line)
    if d.get('duration_ms', 0) > 1000:
        print(f'{d[\"duration_ms\"]:.0f}ms {d[\"message\"]}')"
```

## Known Limitations

1. Team Profile uses `seed_mock_v1` for functional verification only, not real historical data
2. Knockout-stage simulations are simplified and should not be treated as official calculations
3. Free/public data sources may have delays, WAF blocks, field drift, or incomplete coverage
4. AI / intelligence / market features depend on local API token configuration
5. OpenFootball and WorldCup26 providers do not support `live` match status; only football-data.org does

## Disclaimer

All predictions are for informational purposes only and do not constitute betting advice. Football matches inherently involve unpredictability that cannot be fully modeled.

## AI Collaboration

AI agents modifying this project must first read `AI_PROJECT_CONSTRAINTS.md`. Frontend changes also require reading `FRONTEND_UI_RULES.md`. Any new long-term business constraints must be reflected in these files.
