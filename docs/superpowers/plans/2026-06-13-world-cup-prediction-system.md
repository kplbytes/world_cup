# 2026 World Cup Prediction System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local 2026 World Cup dashboard that imports groups A-L and all group matches, predicts every unplayed match, calculates qualification probabilities, and recomputes after final results arrive.

**Architecture:** A FastAPI process owns SQLite persistence, source adapters, prediction/simulation services, scheduled refreshes, and the production React bundle. The React client consumes revision-consistent JSON endpoints and renders group, match, team, source-status, and refresh views. External providers normalize into one domain contract, and a bundled seed snapshot guarantees an immediately usable local application.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy 2, Pydantic 2, Alembic, HTTPX, APScheduler, NumPy, SciPy, pytest; React 19, TypeScript, Vite, TanStack Query, Recharts, Vitest, Testing Library.

**Execution note:** The directory is not a Git repository, so this plan intentionally omits commit steps. Do not initialize or commit unless the user explicitly requests it.

---

## File Structure

```text
backend/
  pyproject.toml                         Python package, runtime and test dependencies
  app/
    main.py                              FastAPI lifecycle, scheduler, static frontend
    config.py                            Environment and filesystem settings
    db.py                                Engine, sessions, WAL configuration
    models.py                            SQLAlchemy persistence models
    schemas.py                           Public API and normalized-provider contracts
    domain/
      standings.py                      FIFA group ranking and third-place selection
    providers/
      base.py                            Provider protocol and normalized payload
      openfootball.py                    No-key baseline fixture adapter
      football_data.py                   Optional token-based live result adapter
      sporttery.py                       Optional schedule/market cross-check adapter
    prediction/
      elo.py                             Historical and incremental Elo ratings
      poisson.py                         xG and score probability matrix
      confidence.py                      Data-based confidence score
      explanation.py                     Deterministic match explanation
    simulation/
      qualification.py                  Group-stage Monte Carlo simulation
    services/
      seed.py                            Bundled seed ingestion
      refresh.py                         Transactional refresh orchestration
      recompute.py                       Standings/predictions/revision publication
      dashboard.py                       Revision-consistent read model
    api/
      routes.py                          Health, dashboard, detail, source, refresh APIs
  tests/
    fixtures/                            Provider and tournament test payloads
    test_seed.py
    test_standings.py
    test_poisson.py
    test_qualification.py
    test_refresh.py
    test_api.py
frontend/
  package.json
  vite.config.ts
  src/
    main.tsx
    api.ts
    types.ts
    App.tsx
    styles.css
    components/
      Header.tsx
      DataSources.tsx
      GroupNav.tsx
      GroupDashboard.tsx
      MatchCard.tsx
      MatchDetail.tsx
      TeamDetail.tsx
      ProbabilityBar.tsx
    test/
      App.test.tsx
data/
  seed/world-cup-2026.json               Normalized 48-team/72-match seed
  raw/.gitkeep                           Optional source snapshots
scripts/
  setup.sh
  dev.sh
  start.sh
.env.example
.gitignore
README.md
```

### Task 1: Local Project Skeleton and Database

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/app/config.py`
- Create: `backend/app/db.py`
- Create: `backend/app/models.py`
- Create: `backend/tests/test_db.py`
- Create: `.gitignore`

- [ ] **Step 1: Write the failing database test**

```python
from sqlalchemy import text

from app.db import create_database, session_scope


def test_database_uses_wal_and_commits(tmp_path):
    create_database(tmp_path / "test.sqlite3")
    with session_scope() as session:
        session.execute(text("CREATE TABLE probe (value INTEGER NOT NULL)"))
        session.execute(text("INSERT INTO probe VALUES (42)"))
    with session_scope() as session:
        assert session.scalar(text("SELECT value FROM probe")) == 42
        assert session.scalar(text("PRAGMA journal_mode")) == "wal"
```

- [ ] **Step 2: Run the test and confirm RED**

Run: `cd backend && python3 -m venv .venv && .venv/bin/pip install -e '.[test]' && .venv/bin/pytest tests/test_db.py -v`

Expected: FAIL because `app.db` does not exist.

- [ ] **Step 3: Implement settings, engine creation, session scope, and core models**

Implement `create_database(path)` so it replaces the module engine/session factory, creates all metadata, enables foreign keys and WAL, and exposes a transactional `session_scope()` context manager. Define focused models for `Team`, `Match`, `TeamRating`, `HistoricalMatch`, `MatchPrediction`, `QualificationPrediction`, `StandingSnapshot`, `DataSnapshot`, `SyncRun`, and `DashboardRevision`; every computed row carries `revision_id` and timestamps.

- [ ] **Step 4: Run the test and confirm GREEN**

Run: `cd backend && .venv/bin/pytest tests/test_db.py -v`

Expected: PASS.

### Task 2: Canonical Seed Dataset and OpenFootball Normalization

**Files:**
- Create: `backend/app/schemas.py`
- Create: `backend/app/providers/base.py`
- Create: `backend/app/providers/openfootball.py`
- Create: `backend/app/services/seed.py`
- Create: `backend/tests/fixtures/openfootball-2026.json`
- Create: `backend/tests/test_seed.py`
- Create: `data/seed/world-cup-2026.json`

- [ ] **Step 1: Add a failing normalization/seed test**

```python
def test_seed_contains_twelve_groups_forty_eight_teams_and_seventy_two_matches(db_session):
    payload = OpenFootballProvider.from_file(FIXTURES / "openfootball-2026.json").load()
    result = seed_tournament(db_session, payload)
    assert result.groups == list("ABCDEFGHIJKL")
    assert result.team_count == 48
    assert result.match_count == 72
    assert db_session.scalar(select(func.count(Team.id))) == 48
    assert db_session.scalar(select(func.count(Match.id))) == 72
```

- [ ] **Step 2: Run and confirm RED**

Run: `cd backend && .venv/bin/pytest tests/test_seed.py -v`

Expected: FAIL because provider and seed service are absent.

- [ ] **Step 3: Implement normalized provider contracts and idempotent seed ingestion**

`TournamentPayload` must contain source metadata, exactly 48 canonical teams assigned four per group, and exactly six matches per group. `seed_tournament()` rejects duplicate teams, unknown team references, invalid group membership, and non-72-match payloads; a second import updates metadata without duplicating rows.

- [ ] **Step 4: Acquire and normalize the current public 2026 dataset**

Fetch the raw OpenFootball 2026 JSON from its documented GitHub raw URL, store the source URL and retrieval time, normalize it to `data/seed/world-cup-2026.json`, and validate it through the same Pydantic contract used at runtime. Do not hand-invent missing teams or scores.

- [ ] **Step 5: Run focused and full tests**

Run: `cd backend && .venv/bin/pytest tests/test_seed.py -v && .venv/bin/pytest -q`

Expected: all tests PASS.

### Task 3: Official Group Standings and Best Third-Place Ranking

**Files:**
- Create: `backend/app/domain/standings.py`
- Create: `backend/tests/test_standings.py`

- [ ] **Step 1: Write a failing points/goal-difference test**

```python
def test_group_table_uses_points_goal_difference_then_goals_scored():
    table = rank_group(teams=["A", "B", "C", "D"], matches=completed_matches_fixture())
    assert [row.team_id for row in table] == ["B", "A", "D", "C"]
    assert table[0].points == 7
```

- [ ] **Step 2: Run and confirm RED**

Run: `cd backend && .venv/bin/pytest tests/test_standings.py::test_group_table_uses_points_goal_difference_then_goals_scored -v`

Expected: FAIL because `rank_group` is absent.

- [ ] **Step 3: Implement points, goals, goal difference, and stable rows**

Return immutable `StandingRow` values containing played, won, drawn, lost, goals for/against, difference, points, and a tie-break uncertainty flag.

- [ ] **Step 4: Add and pass a head-to-head mini-table test**

```python
def test_tied_teams_are_reordered_by_head_to_head_mini_table():
    table = rank_group(teams=TEAMS, matches=head_to_head_tie_fixture())
    assert [row.team_id for row in table[:2]] == ["A", "B"]
```

Run before implementation to see RED, then implement recursive mini-table tie resolution and rerun to GREEN.

- [ ] **Step 5: Add and pass best-eight third-place selection**

```python
def test_best_eight_third_placed_teams_advance():
    ranked = rank_third_placed(third_place_rows_fixture())
    assert len(ranked.qualified) == 8
    assert ranked.qualified[-1].team_id == "G3"
```

Run: `cd backend && .venv/bin/pytest tests/test_standings.py -v`

Expected: PASS.

### Task 4: Elo Ratings and Recent Form

**Files:**
- Create: `backend/app/prediction/elo.py`
- Create: `backend/tests/test_elo.py`
- Create: `backend/app/services/history.py`

- [ ] **Step 1: Write a failing Elo update test**

```python
def test_upset_moves_both_ratings_by_equal_and_opposite_amounts():
    result = update_elo(home=1800, away=1500, home_goals=0, away_goals=1, weight=40)
    assert result.home < 1800
    assert result.away > 1500
    assert pytest.approx(result.home + result.away) == 3300
```

- [ ] **Step 2: Confirm RED, implement FIFA-style expected score and margin multiplier, confirm GREEN**

Run: `cd backend && .venv/bin/pytest tests/test_elo.py -v`

Expected after implementation: PASS.

- [ ] **Step 3: Add time-decayed historical replay and recent-form tests**

Assert replay order, no future-data leakage at a requested cutoff, and a five-match form record such as `WDLDW`. Implement only after observing each test fail.

### Task 5: Poisson Match Prediction Contract

**Files:**
- Create: `backend/app/prediction/poisson.py`
- Create: `backend/app/prediction/confidence.py`
- Create: `backend/app/prediction/explanation.py`
- Create: `backend/tests/test_poisson.py`

- [ ] **Step 1: Write a failing normalized-probability test**

```python
def test_prediction_probabilities_cover_the_full_outcome_space():
    prediction = predict_match(home_strength=0.72, away_strength=0.51, context=neutral_context())
    assert prediction.home_xg > prediction.away_xg
    assert prediction.home_win + prediction.draw + prediction.away_win == pytest.approx(1.0)
    assert sum(item.probability for item in prediction.scorelines) <= 1.0
```

- [ ] **Step 2: Confirm RED**

Run: `cd backend && .venv/bin/pytest tests/test_poisson.py::test_prediction_probabilities_cover_the_full_outcome_space -v`

- [ ] **Step 3: Implement bounded xG conversion and a normalized 0-8 plus tail Poisson matrix**

The public `predict_match()` returns xG, W/D/L, full score matrix, top three scorelines, model inputs, version, and confidence. It must reject NaN/infinite strength inputs.

- [ ] **Step 4: Add deterministic explanation and confidence tests**

Assert explanation text only references provided factors, and stale/missing data lowers confidence even when one side is a strong favorite.

- [ ] **Step 5: Run tests**

Run: `cd backend && .venv/bin/pytest tests/test_poisson.py -v`

Expected: PASS.

### Task 6: Qualification Monte Carlo Simulation

**Files:**
- Create: `backend/app/simulation/qualification.py`
- Create: `backend/tests/test_qualification.py`

- [ ] **Step 1: Write a failing deterministic simulation test**

```python
def test_simulation_is_reproducible_and_returns_all_teams(tournament_fixture):
    first = simulate_qualification(tournament_fixture, iterations=2000, seed=20260613)
    second = simulate_qualification(tournament_fixture, iterations=2000, seed=20260613)
    assert first == second
    assert len(first.teams) == 48
    assert all(p.first + p.second + p.third + p.fourth == pytest.approx(1.0) for p in first.teams)
```

- [ ] **Step 2: Confirm RED**

Run: `cd backend && .venv/bin/pytest tests/test_qualification.py -v`

- [ ] **Step 3: Implement vectorized sampling and official advancement logic**

Lock final matches, sample remaining score matrices, rank all groups, select eight third-place teams, and return placement/qualification frequencies plus Monte Carlo standard error. Use a seeded NumPy generator.

- [ ] **Step 4: Add a completed-group certainty test**

For a group with all six final matches, assert placement probabilities are exactly zero or one and are unaffected by seed.

- [ ] **Step 5: Run tests and benchmark default scale**

Run: `cd backend && .venv/bin/pytest tests/test_qualification.py -v`

Run: `cd backend && .venv/bin/python -m app.simulation.qualification --benchmark --iterations 50000`

Expected: PASS; benchmark completes in a practical local duration and reports elapsed time.

### Task 7: Transactional Recompute and Dashboard Revision

**Files:**
- Create: `backend/app/services/recompute.py`
- Create: `backend/app/services/dashboard.py`
- Create: `backend/tests/test_recompute.py`

- [ ] **Step 1: Write a failing full-recompute test**

```python
def test_recompute_publishes_one_complete_revision(seed_database):
    revision = recompute_all(seed_database, iterations=500, seed=7)
    assert count_predictions(revision.id) == remaining_match_count(seed_database)
    assert count_qualification_rows(revision.id) == 48
    assert active_revision_id(seed_database) == revision.id
```

- [ ] **Step 2: Confirm RED, implement staged rows and atomic publication, confirm GREEN**

The active revision must change only after standings, match predictions, and qualification rows all succeed. A simulated exception must leave the previous revision active.

- [ ] **Step 3: Add dashboard read-model consistency test**

Assert one response contains 12 groups, 48 teams, 72 matches, third-place ranking, source states, and exactly one revision ID.

### Task 8: Live Provider and Post-Match Refresh

**Files:**
- Create: `backend/app/providers/football_data.py`
- Create: `backend/app/services/refresh.py`
- Create: `backend/tests/fixtures/football-data-finished.json`
- Create: `backend/tests/test_refresh.py`

- [ ] **Step 1: Write a failing provider normalization test**

Assert `FINISHED` maps to canonical final status, regulation score fields are selected correctly, source IDs are retained, and unknown teams are rejected.

- [ ] **Step 2: Implement HTTPX provider with token, timeout, rate-limit, and source metadata**

Do not log or expose the token. If no token exists, report provider state `not_configured` rather than failing startup.

- [ ] **Step 3: Write a failing post-match refresh test**

```python
def test_new_final_score_updates_table_ratings_and_revision(seed_database, finished_provider):
    before = snapshot_state(seed_database)
    outcome = refresh_tournament(seed_database, providers=[finished_provider], iterations=500)
    after = snapshot_state(seed_database)
    assert outcome.finalized_matches == 1
    assert after.revision_id != before.revision_id
    assert after.group_points != before.group_points
    assert after.team_elos != before.team_elos
```

- [ ] **Step 4: Implement reconciliation and transactional refresh orchestration**

Record a `SyncRun`, retain raw response checksum, reject conflicting finals, apply accepted changes, then call the same recompute service used at startup/manual refresh.

- [ ] **Step 5: Add failed-provider stale-snapshot test and run suite**

Run: `cd backend && .venv/bin/pytest tests/test_refresh.py -v`

Expected: prior revision remains available and sync failure is recorded.

### Task 8A: Optional China Sports Lottery Cross-Check

**Files:**
- Create: `backend/app/providers/sporttery.py`
- Create: `backend/tests/fixtures/sporttery-calculator.json`
- Create: `backend/tests/test_sporttery.py`
- Modify: `backend/app/models.py`
- Modify: `backend/app/services/refresh.py`

- [ ] **Step 1: Write a failing normalization and de-vig test**

```python
def test_had_odds_are_normalized_without_overround():
    market = normalize_had_prices(home=1.80, draw=3.60, away=4.80)
    assert market.home + market.draw + market.away == pytest.approx(1.0)
    assert market.raw_overround > 1.0
    assert market.home > market.draw > market.away
```

- [ ] **Step 2: Confirm RED, implement bounded parsing, confirm GREEN**

The adapter accepts only positive finite decimal prices, maps match/team aliases through canonical identities, retains source match ID and update time, and returns `unavailable` for WAF/HTML responses instead of treating them as JSON.

- [ ] **Step 3: Add coverage and authority tests**

Assert that omitted Sporttery matches do not delete canonical fixtures, Sporttery scores cannot overwrite primary final scores, and market probabilities remain separate from `MatchPrediction` model probabilities.

- [ ] **Step 4: Integrate as a non-blocking refresh source**

Persist market snapshots and source status. Expose them only when all three HAD prices exist and the match maps unambiguously.

- [ ] **Step 5: Run tests**

Run: `cd backend && .venv/bin/pytest tests/test_sporttery.py tests/test_refresh.py -v`

Expected: PASS, including a WAF response fixture that degrades gracefully.

### Task 9: FastAPI Contract and Local Scheduler

**Files:**
- Create: `backend/app/api/routes.py`
- Create: `backend/app/main.py`
- Create: `backend/tests/test_api.py`

- [ ] **Step 1: Write failing API integration tests against temporary SQLite**

Test `/api/health`, `/api/dashboard`, group/match/team detail, source/sync history, and `POST /api/refresh`. Assert 404 for unknown entities and that tokens never appear in responses.

- [ ] **Step 2: Implement routes and lifespan**

Startup creates/loads the database, publishes a seed revision if needed, schedules refresh without blocking first paint, and shuts down APScheduler cleanly. Bind defaults remain loopback-only.

- [ ] **Step 3: Add scheduler interval test**

Assert normal polling uses 15 minutes and live-window polling uses 2 minutes, with both configurable through settings.

- [ ] **Step 4: Run API suite**

Run: `cd backend && .venv/bin/pytest tests/test_api.py -v`

Expected: PASS.

### Task 10: React Dashboard Tracer Slice

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/tsconfig.json`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/api.ts`
- Create: `frontend/src/types.ts`
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/styles.css`
- Create: `frontend/src/components/Header.tsx`
- Create: `frontend/src/components/DataSources.tsx`
- Create: `frontend/src/components/GroupNav.tsx`
- Create: `frontend/src/components/GroupDashboard.tsx`
- Create: `frontend/src/components/MatchCard.tsx`
- Create: `frontend/src/components/ProbabilityBar.tsx`
- Create: `frontend/src/test/App.test.tsx`

- [ ] **Step 1: Scaffold Vite dependencies and write a failing user-flow test**

```tsx
it("switches from Group A to Group L and shows its six matches", async () => {
  render(<App />, { wrapper: testQueryClient });
  await screen.findByRole("heading", { name: "Group A" });
  await userEvent.click(screen.getByRole("button", { name: "L" }));
  expect(await screen.findByRole("heading", { name: "Group L" })).toBeVisible();
  expect(screen.getAllByTestId("match-card")).toHaveLength(6);
});
```

- [ ] **Step 2: Run and confirm RED**

Run: `cd frontend && npm install && npm test -- --run`

Expected: FAIL because the app components are absent.

- [ ] **Step 3: Implement the dashboard slice**

Render header/status cards, A-L navigation, four team cards, six match cards, probability bars, qualification panel, responsive desktop/mobile layouts, explicit stale/unavailable states, and Asia/Shanghai dates. Use API data only; no hard-coded predictions in components.

- [ ] **Step 4: Run test, typecheck, and build**

Run: `cd frontend && npm test -- --run && npm run typecheck && npm run build`

Expected: PASS and `frontend/dist` produced.

### Task 11: Match and Team Details, All-Matches View, Refresh UX

**Files:**
- Create: `frontend/src/components/MatchDetail.tsx`
- Create: `frontend/src/components/TeamDetail.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/src/test/App.test.tsx`

- [ ] **Step 1: Add one failing test for opening match details**

Assert W/D/L, xG, three scorelines, explanation, recent form, model version, and confidence warning are visible.

- [ ] **Step 2: Implement and confirm GREEN**

- [ ] **Step 3: Add one failing test for team detail and missing squad enrichment**

Assert FIFA points/rank, Elo, recent results, remaining fixtures, and an explicit `Squad data unavailable` state.

- [ ] **Step 4: Implement and confirm GREEN**

- [ ] **Step 5: Add failing tests for all-match filtering and manual refresh status**

Assert filters for scheduled/live/final and that refresh disables the button, reports outcome, then invalidates dashboard queries.

- [ ] **Step 6: Implement and run frontend verification**

Run: `cd frontend && npm test -- --run && npm run typecheck && npm run build`

Expected: PASS.

### Task 12: Local Setup, Production Serving, Documentation, and Final Audit

**Files:**
- Create: `scripts/setup.sh`
- Create: `scripts/dev.sh`
- Create: `scripts/start.sh`
- Create: `.env.example`
- Create: `README.md`
- Modify: `backend/app/main.py`
- Modify: `progress.md`

- [ ] **Step 1: Write shell scripts with strict mode and portable project-root resolution**

`setup.sh` installs editable backend/test dependencies and frontend dependencies. `dev.sh` runs backend and Vite with cleanup traps. `start.sh` builds the frontend if missing/stale and runs Uvicorn on `127.0.0.1:8000`.

- [ ] **Step 2: Serve `frontend/dist` and preserve API 404 behavior**

Mount assets explicitly and use an SPA fallback only for non-API routes.

- [ ] **Step 3: Document exact setup, launch, optional API token, sources, model, refresh semantics, backup location, and limitations**

README must state that player values are optional, predictions are informational, and the last snapshot remains visible on provider failure.

- [ ] **Step 4: Run all automated verification**

Run:

```bash
cd backend && .venv/bin/pytest -q
cd ../frontend && npm test -- --run && npm run typecheck && npm run build
cd .. && ./scripts/start.sh
```

Expected: backend and frontend suites PASS; server starts at `http://127.0.0.1:8000`.

- [ ] **Step 5: Perform browser acceptance using the Browser plugin**

Verify A-L navigation, 72 matches, group table, third-place table, match/team detail, source timestamps, stale state, and manual refresh. Capture a mobile-width check and confirm no horizontal overflow.

- [ ] **Step 6: Prove post-match behavior against a copied local database**

Apply the deterministic finished-match fixture through the refresh service and verify the active revision, standings, Elo, remaining match predictions, and qualification probabilities all change while the prior revision remains auditable.

- [ ] **Step 7: Update planning artifacts with final evidence**

Record exact test totals, build output, browser checks, source coverage, and any known external-provider limitation in `progress.md` and `findings.md`.

---

## Completion Evidence Matrix

| Requirement | Authoritative evidence |
|---|---|
| Groups A-L, 48 teams, 72 matches | Seed validation test, database counts, browser dashboard |
| Every unplayed match predicted | Revision completeness test and dashboard API count |
| W/D/L, xG, scorelines, explanation | Poisson tests, match-detail test, browser inspection |
| Qualification probabilities | Simulation tests, 48 persisted rows, UI probability panel |
| Official group and third-place rules | Domain tie-break test suite |
| Data sources and freshness | `data_snapshots`, source API, source status cards |
| Final result updates system state | Post-match refresh integration test and copied-DB audit |
| Local-only operation | Setup/start scripts and loopback browser run |
| Provider outage resilience | Failed-provider test and stale UI state |
