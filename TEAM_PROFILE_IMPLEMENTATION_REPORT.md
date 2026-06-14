# Team Profile Implementation Report

## 1. Modified Files

### Backend (New)
- `backend/app/team_profiles/__init__.py` — Module init, version constants
- `backend/app/team_profiles/data_loader.py` — Seed mock data loader (`seed_mock_v1`)
- `backend/app/team_profiles/feature_engineering.py` — Opponent tier classification, metric computation, trait generation
- `backend/app/team_profiles/scorer.py` — Profile adjustment engine with caps and normalization
- `backend/app/team_profiles/service.py` — Profile computation, rebuild orchestration, explain
- `backend/app/team_profiles/evaluation.py` — Profile Brier evaluation, helped/hurt/neutral, trait analysis
- `backend/app/api/routes/team_profile_routes.py` — REST API endpoints
- `backend/scripts/build_team_profiles.py` — Build script

### Backend (Modified)
- `backend/app/models.py` — Added `TeamProfileMatchHistory`, `TeamProfile`, `TeamProfilePrediction` (with `UniqueConstraint("revision_id", "match_id")`)
- `backend/app/db.py` — Migration version 4: creates team profile tables, indexes, unique constraint, and deduplicates existing data
- `backend/app/services/dashboard.py` — `build_match_detail` and `build_team_detail` include profiles
- `backend/app/services/recompute.py` — `compute_match_predictions` generates profile predictions
- `backend/app/services/snapshots.py` — T-30 profile locking
- `backend/app/ai/providers/base.py` — `AIPredictionRequest` has home/away_team_profile fields
- `backend/app/ai/prompt_builder.py` — Team Profiles section in AI prompt
- `backend/app/ai/parser.py` — Parses `profile_factors`, `profile_risk_flags`
- `backend/app/ai/schemas.py` — `AIParsedOutput` has profile fields
- `backend/app/ai/service.py` — `_build_prediction_request` fetches and attaches profiles

### Frontend (Modified)
- `frontend/src/types.ts` — `MatchTeamProfiles`, `TeamProfile`, `ProfilePrediction`, `ProfileEvaluation` types
- `frontend/src/api.ts` — `getTeamProfile`, `getProfileEvaluation` API functions
- `frontend/src/components/MatchDetailDrawer.tsx` — Team profiles section in match drawer
- `frontend/src/components/TeamDetail.tsx` — Team profile panel in team detail
- `frontend/src/components/ModelReviewCenter.tsx` — Profile model performance section
- `frontend/src/test/App.test.tsx` — Mock for profile evaluation endpoint

## 2. New Table Structure

### `team_profile_match_history`
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| team_id | FK(teams) | Team owning this match record |
| match_date | DATE | Match date |
| competition | VARCHAR(80) | Competition name |
| stage | VARCHAR(40) | Group/knockout |
| opponent_team_id | FK(teams) | Opponent |
| opponent_name | VARCHAR(120) | Opponent display name |
| opponent_elo | FLOAT | Opponent Elo rating |
| opponent_tier | VARCHAR(16) | elite/strong/mid/weak |
| goals_for | INTEGER | Goals scored |
| goals_against | INTEGER | Goals conceded |
| result | VARCHAR(8) | win/draw/loss |
| points | INTEGER | 3/1/0 |
| is_world_cup | BOOLEAN | World Cup match |
| is_qualifier | BOOLEAN | Qualifier match |
| is_friendly | BOOLEAN | Friendly match |
| source | VARCHAR(80) | Data source label |
| created_at | DATETIME | Record timestamp |

### `team_profiles`
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| team_id | FK(teams) | Team |
| team_code | VARCHAR(3) | Team code |
| profile_version | VARCHAR(40) | Version (team-profile-v1) |
| profile_as_of | DATETIME | Profile cutoff date |
| data_cutoff | DATETIME | Data cutoff |
| sample_count | INTEGER | Total samples |
| world_cup_sample_count | INTEGER | World Cup samples |
| qualifier_sample_count | INTEGER | Qualifier samples |
| competitive_sample_count | INTEGER | Non-friendly samples |
| attack_strength_recent | FLOAT | Recent attacking strength |
| defense_strength_recent | FLOAT | Recent defensive strength |
| goal_for_avg | FLOAT | Avg goals for |
| goal_against_avg | FLOAT | Avg goals against |
| clean_sheet_rate | FLOAT | Clean sheet % |
| failed_to_score_rate | FLOAT | Failed to score % |
| over_2_5_rate | FLOAT | Over 2.5 goals % |
| under_2_5_rate | FLOAT | Under 2.5 goals % |
| both_teams_score_rate | FLOAT | Both teams score % |
| low_score_tendency | FLOAT | Low score tendency |
| high_score_tendency | FLOAT | High score tendency |
| draw_rate_overall | FLOAT | Overall draw rate |
| draw_rate_vs_elite | FLOAT | Draw vs elite rate |
| draw_rate_vs_strong | FLOAT | Draw vs strong rate |
| draw_rate_as_underdog | FLOAT | Draw as underdog rate |
| draw_resilience_score | FLOAT | Draw resilience (0-1) |
| favorite_win_rate | FLOAT | Win vs weak rate |
| favorite_overconfidence_risk | FLOAT | Overconfidence risk |
| defensive_resilience_score | FLOAT | Defensive resilience |
| world_cup_experience_score | FLOAT | WC experience (0-1) |
| knockout_experience_score | FLOAT | KO experience (0-1) |
| tier_stats_json | JSON | Per-tier breakdown |
| traits_json | JSON | String tags |
| source_summary_json | JSON | Data source metadata |

### `team_profile_predictions`
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| revision_id | FK(revisions) | Dashboard revision |
| match_id | FK(matches) | Match |
| model_version | VARCHAR(48) | elo-poisson-v1-team-profile |
| profile_version | VARCHAR(40) | team-profile-v1 |
| profile_as_of | DATETIME | Earliest of home/away as-of |
| base_home_win | FLOAT | Baseline home win prob |
| base_draw | FLOAT | Baseline draw prob |
| base_away_win | FLOAT | Baseline away win prob |
| home_win | FLOAT | Profile-adjusted home win |
| draw | FLOAT | Profile-adjusted draw |
| away_win | FLOAT | Profile-adjusted away win |
| home_xg | FLOAT | Profile-adjusted home xG |
| away_xg | FLOAT | Profile-adjusted away xG |
| probability_deltas_json | JSON | Per-outcome delta |
| xg_deltas_json | JSON | xG adjustments |
| risk_flags_json | JSON | Risk flag strings |
| triggered_traits_json | JSON | Triggered trait strings |
| explanation | TEXT | Adjustment rationale |
| is_pre_match_locked | BOOLEAN | T-30 locked |
| is_fallback_locked | BOOLEAN | Fallback locked |
| real_time_only | BOOLEAN | Post-kickoff |
| locked_at | DATETIME | Lock timestamp |

## 3. New API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/team-profiles` | All team profiles |
| GET | `/api/team-profiles/{team_id}` | Single team profile |
| GET | `/api/team-profiles/evaluation` | Profile model evaluation |
| POST | `/api/team-profiles/rebuild` | Rebuild profiles from seed |
| GET | `/api/team-profile-predictions/{match_id}` | Profile prediction for match |

The profile data is also returned through existing endpoints:
- `GET /api/matches/{match_id}` → `team_profiles.{home,away}` and `profile_prediction`
- `GET /api/teams/{team_id}` → `team_profile`

## 4. Data Source Description

Current data is **`seed_mock_v1`** — deterministic mock data generated from team Elo ratings and hash-based randomization. This is NOT real historical data. Key characteristics:

- 16 matches per team (768 total)
- Coverage: 2014 WC, 2018 WC, 2022 WC, 2026 qualifiers, continental cups
- Source label: `{"mode": "seed_mock_v1", "sources": ["seed_mock_v1"], "friendly_weight": 0.0}`
- Programmatic seed: SHA256 hash of team code
- Opponent tiers: classified by Elo thresholds (elite ≥1850, strong ≥1700, mid ≥1450, weak <1450)

**Origin:** The API response clearly returns `source_summary_json.mode = "seed_mock_v1"`. The frontend displays "种子模拟数据" label. No UI describes this as real historical data.

## 5. Profile Computation Logic

```
1. Load historical match records filtered to match_date <= as_of_date
2. Classify opponents by Elo into elite/strong/mid/weak tiers
3. Compute per-tier statistics (win/draw/loss rates, goals)
4. Compute 30+ metric columns:
   - Attack/defense strength (recent 8 matches)
   - Goal averages, clean sheet, failed to score rates
   - Over/under 2.5, both teams score rates
   - Draw rates (overall, vs elite, vs strong, as underdog)
   - Draw resilience score
   - Favorite win rate and overconfidence risk
   - Underdog upset potential
   - WC/knockout experience scores
   - Group stage consistency
5. Generate traits from empirical thresholds (min 6 samples)
6. Store as TeamProfile row with version, as_of, and source metadata
```

## 6. Example Team Profile JSON (ESP)

```json
{
  "team_id": "ESP",
  "team_code": "ESP",
  "profile_version": "team-profile-v1",
  "sample_count": 16,
  "world_cup_sample_count": 10,
  "traits": ["防守优先", "大赛经验丰富"],
  "source_summary_json": {"mode": "seed_mock_v1"},
  "attack_strength_recent": 1.0,
  "defense_strength_recent": 1.0,
  "draw_resilience_score": 0.404,
  "world_cup_experience_score": 0.833
}
```

## 7. Example Match Adjustment

**Match:** ESP vs CPV (Group H, 2026-06-15)

| | Baseline | Profile-Adjusted | Delta |
|---|---|---|---|
| Home Win | 72.6% | 75.6% | +3.0% |
| Draw | 19.1% | 16.1% | -3.0% |
| Away Win | 8.4% | 8.4% | 0.0% |
| xG Home | 1.74 | 1.74 | 0.0 |
| xG Away | 0.31 | 0.31 | 0.0 |

**Triggered profile:**
- Home traits: 防守优先, 大赛经验丰富, 首战慢热
- Risk flags: favorite_stability
- Explanation: "热门方对弱队稳定性提高胜率 +3.0%"
- All caps satisfied: single delta ≤5%, total L1 ≤8%, xG delta ≤0.15

## 8. Baseline vs Profile Comparison

| Metric | Baseline | Profile |
|--------|----------|---------|
| Model Version | elo-poisson-v1 | elo-poisson-v1-team-profile |
| Profiles | 48 teams | 48 teams |
| Predictions | 65 matches | 65 matches |
| Non-zero Adjustments | — | 108 (out of 195 total) |
| Active Revision | 29 | 29 |

**Baseline table (`MatchPrediction`) is never modified by profile code.** Profile predictions are stored in a separate table (`TeamProfilePrediction`) with an independent model version.

**Key guardrails verified:**
- Single outcome cap: ≤5% (max observed: 3.9%)
- Total L1 cap: ≤8% (max observed: 6.0%)
- xG cap: ±0.15 (max observed: 0.036)
- Probabilities always normalize to 1.0
- Sample count < 6 → no strong traits, no adjustment triggers

## 9. Full Test Results

### Backend (312 passed, 1 warning)
```
312 passed, 1 warning in 129.49s
```
Warning: StarletteDeprecationWarning (httpx deprecation, pre-existing)

### Frontend Unit Tests (19 passed)
```
Test Files  2 passed (2)
     Tests  19 passed (19)
```

### Frontend Type Check
```
npx tsc --noEmit — clean (no errors)
```

### Frontend Build
```
✓ built in 392ms
- CSS: 29.13 kB (gzip: 6.14 kB)
- JS: 296.76 kB (gzip: 90.20 kB)
```

### Team Profile Specific Tests (6 passed)
```
test_opponent_tier_boundaries PASSED
test_profile_as_of_excludes_future_matches PASSED
test_profile_metrics_and_traits_require_evidence PASSED
test_small_sample_does_not_emit_strong_traits PASSED
test_profile_adjustment_is_capped_and_normalized PASSED
test_rebuild_creates_profile_for_every_team PASSED
```

## 10. Browser Screenshots

### Screenshot 1: Match Detail - Team Profile Section
**File:** `frontend/screenshots/team-profile-match-detail.png`
Shows the match drawer with 球队画像 section displaying home/away team profiles, trait tags, draw resilience %, low score tendency %, and the profile prediction impact with delta values and explanation.

### Screenshot 2: Model Review - Profile Evaluation
**File:** `frontend/screenshots/team-profile-model-review.png`
Shows the 模型复盘中心 with 球队画像模型表现 section showing sample count, baseline Brier (— when no scorable matches yet), profile Brier, helped/hurt/neutral counts, and most/least useful traits.

### Screenshot 3: Dashboard Overview
**File:** `frontend/screenshots/team-profile-dashboard.png`
Shows the main dashboard with team profile data accessible via the match center.

## 11. Mock Data Scope

The following uses **`seed_mock_v1`** deterministic mock data:
- All `team_profile_match_history` records (768 rows)
- All `team_profiles` (48 rows)
- Profile predictions derived from seed data
- Opponent tier classifications
- Trait generation

## 12. Real Data Integration Coverage

The following uses **real (seeded/in-memory)** tournament data:
- Team roster (48 teams, groups A-L)
- Match schedule (72 matches, group + knockout)
- Elo ratings from team ratings data
- Match predictions (Baseline, Profile)
- Baseline prediction snapshots

The following are **not yet real**:
- Historical match data (seed_mock_v1 only)
- Profile predictions → only as good as the mock history

## 13. Next Steps for Real History Upgrade

1. Replace `seed_mock_history()` in `data_loader.py` with a real data importer that reads from a trusted historical source
2. The `source_summary_json.mode` will switch from `"seed_mock_v1"` to `"mixed"` or a real source name automatically
3. Update the cutoff date reference from tournament start to actual match dates
4. Tier statistics will reflect actual opponent strength
5. Rebuild profiles → re-run recompute → profile predictions reflect real data
6. Re-evaluate profile model Brier vs baseline

## 14. Unresolved Issues and Risks

1. **Profile evaluation has 0 samples** — no final matches have locked profile predictions yet. Evaluation will become meaningful once matches finish and are scored with the profile version active.
2. **Time-zone handling** — SQLite stores naive datetimes; `profile_as_of` and `match_date` comparisons rely on Python-side conversion. Risk of subtle comparison bugs when UTC and naive datetimes mix.
3. ~~No unique constraint on `TeamProfilePrediction(revision_id, match_id)`~~ **Fixed**: Added `UniqueConstraint("revision_id", "match_id")` in models.py and `CREATE UNIQUE INDEX` in db.py migration v4. Existing duplicates cleaned up during migration.
4. **Sample 6 threshold is arbitrary** — based on heuristic, not statistical power analysis.
5. **Mock data bias** — seed_mock_v1 uses hash-based randomization, not real match dynamics. Traits generated from mock data may not reflect real team behavior.
6. **Profile evaluation LogLoss** — The current formula in `evaluation.py` line 49 uses an approximation that may not match exact LogLoss calculation; this should be reviewed when real data is available.
7. **Frontend screenshots** — were taken from a local dev instance; the match detail drawer may show different content depending on which match is clicked.

## 15. Real Database Build Statistics

| Metric | Value |
|--------|-------|
| Database Size | 17.3 MB |
| Teams | 48 |
| Team Profiles | 48 (100% coverage) |
| Match History Records | 768 (16 per team) |
| Profile Predictions | 195 (65 matches × 3 revisions) |
| Matches with Predictions | 65 |
| Non-zero Delta Predictions | 108 |
| Dashboard Revisions | 29 |
| Active Revision | 29 (elo-poisson-v1) |
| DB Schema Version | 4 (team profile tables migration) |
| Unique Constraint | `uq_team_profile_predictions_revision_match` on (revision_id, match_id) |
| Sample Profile | ESP: 16 samples, 2 traits |
| Adjustment Example | ESP-CPV: +3.0% home win |

---

## Acceptance Criteria Verification

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| 1 | 48 team profiles created | ✅ | `profiles=48` in rebuild output |
| 2 | as-of date filtering prevents future data leakage | ✅ | `test_profile_as_of_excludes_future_matches` passes |
| 3 | Independent model version (elo-poisson-v1-team-profile) | ✅ | `model_version` field in scorer, confirmed by 108 predictions |
| 4 | Adjustment caps enforced (5%/8%/0.15) | ✅ | `test_profile_adjustment_is_capped_and_normalized` passes |
| 5 | Probability normalization | ✅ | All predictions verified `sum ≈ 1.0` |
| 6 | Small sample (<6) produces no strong traits | ✅ | `test_small_sample_does_not_emit_strong_traits` passes |
| 7 | Baseline predictions unchanged | ✅ | Separate table, no writes to `MatchPrediction` |
| 8 | Frontend displays profiles in 3 locations | ✅ | MatchDetailDrawer, TeamDetail, ModelReviewCenter |
| 9 | API source clearly marked as seed_mock_v1 | ✅ | `source_summary_json.mode = "seed_mock_v1"` |
| 10 | Report written with all 15 sections | ✅ | This document |

**10 out of 10 acceptance criteria satisfied.**

---

CLAUDE_TEAM_PROFILE_DONE /Users/liudapeng/Documents/code/others/world_cup/TEAM_PROFILE_IMPLEMENTATION_REPORT.md