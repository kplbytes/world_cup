# 2026 World Cup Prediction System Design

## 1. Scope

The system is a single-user local web application for the 2026 FIFA World Cup. It must:

- Display all 12 groups (A-L), all 48 teams, and all 72 group-stage matches.
- Display current group tables and the ranking of third-placed teams.
- Predict win/draw/loss probability, expected goals, and likely scorelines for every unplayed match.
- Calculate first-place, second-place, third-place, and overall qualification probabilities for every team.
- Explain the main factors behind each prediction without claiming certainty.
- Show every data source, fetch time, coverage status, and model confidence.
- Detect completed matches while the local application is running, store final scores, update standings and team strength, and recompute all remaining predictions.
- Continue to display the last successful snapshot when external sources are unavailable.

Docker, cloud hosting, accounts, betting features, and multi-user collaboration are out of scope.

## 2. User Experience

The application opens as a dark, data-dense dashboard inspired by the reference screenshots without copying its branding.

### Main dashboard

- Header: last successful refresh, next scheduled refresh, model version, data confidence, and refresh button.
- Data status cards: fixtures/results, FIFA rankings, historical results, and optional squad enrichment.
- Group selector: A-L.
- Views: selected group and all matches.
- Team cards: FIFA rank/points, model Elo, recent form, group record, and qualification probabilities.
- Match cards: kickoff time in Asia/Shanghai, venue, status, score or predicted probabilities, expected goals, likely scorelines, and confidence.
- Side panel: qualification probability chart and concise model methodology.

### Match detail

- Win/draw/loss probability and expected goals.
- Top scoreline probabilities derived from the score matrix.
- Team strength comparison and recent-form summary.
- Plain-language explanation generated from deterministic model inputs and templates, not from fabricated facts.
- Data completeness and confidence warnings.

### Team detail

- Group, FIFA ranking points, model Elo, recent international results, current group statistics, and remaining fixtures.
- Optional squad information when a reliable source is available; missing squad/value fields remain visibly unavailable.

## 3. Local Architecture

### Backend

- Python 3.12+
- FastAPI HTTP API and static frontend hosting
- SQLAlchemy 2 with SQLite
- APScheduler for local background refreshes
- NumPy/SciPy for probability calculations and simulations
- HTTPX for external data access

The backend is divided into focused modules:

- `providers`: external-source adapters returning normalized records.
- `ingestion`: validation, reconciliation, provenance, and database writes.
- `domain`: tournament entities and official ranking rules.
- `prediction`: Elo updates, expected-goals model, score matrix, and explanations.
- `simulation`: repeated simulation of all remaining group matches.
- `services`: refresh orchestration and dashboard queries.
- `api`: stable JSON interfaces consumed by the frontend.

### Frontend

- React + TypeScript + Vite
- TanStack Query for API state
- Recharts for probability and comparison charts
- CSS variables and scoped component styles; no generic component framework is required

### Local launch

- `./scripts/setup.sh` creates the Python environment and installs backend/frontend dependencies.
- `./scripts/dev.sh` starts backend and Vite development servers.
- `./scripts/start.sh` builds the frontend when needed and starts one local FastAPI process.
- The default address is `http://127.0.0.1:8000`.

## 4. Data Model

Core SQLite tables:

- `teams`: canonical identity, group, country codes, display names.
- `team_aliases`: provider-specific name mapping.
- `matches`: official fixture identity, kickoff, venue, status, teams, scores, and last source update.
- `team_ratings`: FIFA points/rank, model Elo, effective date, and source.
- `historical_matches`: normalized training/form data.
- `standings_snapshots`: table rows for each recomputation revision.
- `match_predictions`: model inputs, W/D/L probabilities, xG, scorelines, confidence, and model version.
- `qualification_predictions`: first/second/third/qualify probabilities and simulation metadata.
- `data_snapshots`: raw response checksum, source URL, fetch time, status, and optional local file path.
- `sync_runs`: start/end time, provider outcome, inserted/updated counts, warnings, and errors.

Scores and source snapshots are append-auditable. Current entities are updated transactionally only after an incoming payload passes validation.

## 5. Data Sources and Reconciliation

### Baseline fixtures and teams

- Primary no-key baseline: OpenFootball `worldcup.json` 2026 dataset.
- Bundled seed snapshot: a normalized project-owned JSON file created from the public dataset so first launch works without a token.

### Live fixtures and results

- Preferred provider: football-data.org World Cup feed when `FOOTBALL_DATA_API_TOKEN` is configured.
- No-key fallback: a replaceable community World Cup provider, accepted only after schema and score validation.
- FIFA's official match page is displayed as the authority link and used for manual conflict verification. The application will not depend on brittle HTML scraping for normal operation.
- Optional China Sports Lottery adapter: use its public football calculator feed only for schedule/status cross-checks and HAD market odds. Because it may omit non-sale matches and may reject automated requests through WAF, its failure never blocks refresh and its scores never override a validated final result from the primary feed.

### Team strength

- Official FIFA ranking/ranking points, with effective date and source URL.
- Public-domain international match history for model training and recent form.
- Locally persisted model Elo updated from imported history and new final results.

### Squad enrichment

- Optional and isolated from prediction correctness.
- No fabricated transfer values or players.
- Provider failures reduce enrichment coverage, not application availability.

### Reconciliation rules

1. Canonical match identity uses tournament, stage, teams, and kickoff window rather than provider IDs alone.
2. Final scores outrank scheduled/live values.
3. A conflicting final score is never silently overwritten; it creates a visible sync warning.
4. Unknown teams, duplicate matches, invalid scores, or impossible group membership reject the payload.
5. Every accepted field records provider and fetch timestamp.

## 6. Prediction Model

The initial model is transparent and reproducible rather than an opaque AI claim.

### Strength rating

- Start from historical international results.
- Maintain an Elo rating with match-importance weighting and time decay.
- Blend normalized model Elo with current FIFA ranking points.
- Add recent-form adjustment from the last five eligible internationals.
- Add a small host/home-context adjustment only where supported by match metadata.

### Expected goals and score probabilities

- Convert the relative attacking/defensive strength proxy into bounded home and away expected goals.
- Generate a 0-8 goal Poisson matrix, retaining a tail bucket so total probability remains one.
- Sum matrix cells into win/draw/loss probabilities.
- Report the highest-probability scorelines and each side's expected goals.
- Calibrate coefficients using held-out historical international matches; store calibration metrics and model version.
- When complete Sporttery HAD prices are available, show a separate de-vigged market probability comparison. Do not blend it into the core model until a documented backtest proves an improvement on held-out matches.

### Confidence

Confidence is derived from data freshness, ranking coverage, historical sample coverage, provider agreement, and model calibration. It is never inferred from how decisive the prediction looks.

## 7. Standings and Qualification Simulation

Official group ranking is applied in this order:

1. Points.
2. Group goal difference.
3. Group goals scored.
4. Head-to-head criteria among tied teams.
5. Fair-play or drawing-lots fields when available.

When unavailable tie-break inputs remain exactly tied, simulations use a deterministic seeded fallback and mark the affected probability as tie-break uncertain.

For each recomputation:

1. Lock completed match scores.
2. Sample every remaining match from its full score probability matrix.
3. Rank each of the 12 groups.
4. Rank all 12 third-placed teams and advance the best eight.
5. Repeat with a fixed reproducible seed and a configurable default of 50,000 simulations.
6. Store first, second, third, and qualification frequencies plus Monte Carlo standard error.

## 8. Refresh Lifecycle

- On startup, load the database immediately and serve the last snapshot.
- Run a refresh after startup without blocking the UI.
- Default polling interval: 15 minutes, configurable in `.env`.
- During a live-match window, poll every 2 minutes when the configured provider permits it.
- Manual refresh uses the same orchestration path and reports progress/status.
- When a match changes to final:
  1. Validate and commit the result.
  2. Recompute standings.
  3. Update model Elo and recent form.
  4. Recompute all unplayed match predictions.
  5. Rerun qualification simulation.
  6. Publish a new dashboard revision atomically.

If refresh fails, the previous revision remains active and the UI displays the failed source and error time.

## 9. API Contract

Minimum endpoints:

- `GET /api/health`
- `GET /api/dashboard`
- `GET /api/groups/{group_code}`
- `GET /api/matches`
- `GET /api/matches/{match_id}`
- `GET /api/teams/{team_id}`
- `GET /api/data-sources`
- `GET /api/sync-runs`
- `POST /api/refresh`

Dashboard responses include a revision ID so the frontend never combines objects from different recomputation revisions.

## 10. Error Handling

- External HTTP operations use bounded timeouts and limited retry with backoff.
- Invalid upstream payloads are recorded but never partially applied.
- SQLite writes use transactions and WAL mode.
- Background exceptions are visible through sync history and data-status cards.
- Empty or stale fields render explicit states such as `Unavailable` or `Last updated ...`; the application never invents values.

## 11. Testing and Verification

### Backend tests

- Provider normalization fixtures and conflict handling.
- Official standings and head-to-head tie-break examples.
- Best-eight third-place selection.
- Probability matrix sums and W/D/L consistency.
- Deterministic qualification simulations with fixed seeds.
- Post-match refresh transaction and complete recomputation.
- API integration tests against a temporary real SQLite database.

### Frontend tests

- Group navigation and all-match filtering.
- Loading, stale, unavailable, and failed-refresh states.
- Match/team detail rendering and probability formatting.
- Manual refresh behavior.

### End-to-end acceptance

- All groups A-L and all 72 group matches are present.
- Every unplayed match has a normalized prediction totaling 100% within rounding tolerance.
- Every team has qualification probabilities totaling consistently across placement buckets.
- Applying a final score changes standings and triggers a new prediction revision.
- Restarting the program preserves all accepted results and the last dashboard revision.
- The production frontend is exercised in a real browser at `127.0.0.1:8000`.

## 12. Security and Data Ethics

- The server binds to loopback by default.
- API tokens are read from `.env`, excluded from version control, and never returned by APIs.
- The product states that predictions are informational and not betting advice.
- Source licenses and attribution are included in the application and README.
