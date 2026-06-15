from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import logging
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import Base

logger = logging.getLogger(__name__)


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _configure_sqlite(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

def _upgrade_schema(engine: Engine) -> None:
    """Lightweight and idempotent SQLite schema migration."""
    with engine.begin() as conn:
        version = conn.scalar(text("PRAGMA user_version"))

        try:
            if version < 1:
                logger.info("Upgrading database schema to version 1...")

                # Check prediction_snapshots
                snapshots_info = conn.execute(text("PRAGMA table_info(prediction_snapshots)")).mappings().all()
                if snapshots_info:
                    cols = {row["name"] for row in snapshots_info}
                    if "is_pre_match_locked" not in cols:
                        conn.execute(text("ALTER TABLE prediction_snapshots ADD COLUMN is_pre_match_locked BOOLEAN NOT NULL DEFAULT 0"))
                    if "is_fallback_locked" not in cols:
                        conn.execute(text("ALTER TABLE prediction_snapshots ADD COLUMN is_fallback_locked BOOLEAN NOT NULL DEFAULT 0"))
                    if "kickoff" not in cols:
                        conn.execute(text("ALTER TABLE prediction_snapshots ADD COLUMN kickoff DATETIME"))
                    if "has_auto_adjustments" not in cols:
                        conn.execute(text("ALTER TABLE prediction_snapshots ADD COLUMN has_auto_adjustments BOOLEAN NOT NULL DEFAULT 0"))
                    if "base_home_win" not in cols:
                        conn.execute(text("ALTER TABLE prediction_snapshots ADD COLUMN base_home_win FLOAT"))
                    if "base_draw" not in cols:
                        conn.execute(text("ALTER TABLE prediction_snapshots ADD COLUMN base_draw FLOAT"))
                    if "base_away_win" not in cols:
                        conn.execute(text("ALTER TABLE prediction_snapshots ADD COLUMN base_away_win FLOAT"))

                    # Create indexes for prediction_snapshots
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_prediction_snapshots_kickoff ON prediction_snapshots(kickoff)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_prediction_snapshots_is_pre_match_locked ON prediction_snapshots(is_pre_match_locked)"))

                # Check match_predictions
                predictions_info = conn.execute(text("PRAGMA table_info(match_predictions)")).mappings().all()
                if predictions_info:
                    cols = {row["name"] for row in predictions_info}
                    if "has_auto_adjustments" not in cols:
                        conn.execute(text("ALTER TABLE match_predictions ADD COLUMN has_auto_adjustments BOOLEAN NOT NULL DEFAULT 0"))
                    if "base_home_win" not in cols:
                        conn.execute(text("ALTER TABLE match_predictions ADD COLUMN base_home_win FLOAT"))
                    if "base_draw" not in cols:
                        conn.execute(text("ALTER TABLE match_predictions ADD COLUMN base_draw FLOAT"))
                    if "base_away_win" not in cols:
                        conn.execute(text("ALTER TABLE match_predictions ADD COLUMN base_away_win FLOAT"))

                conn.execute(text("PRAGMA user_version = 1"))

            if version < 2:
                logger.info("Upgrading database schema to version 2 (P2+ tournament + AI)...")

                # Add tournament fields to matches
                matches_info = conn.execute(text("PRAGMA table_info(matches)")).mappings().all()
                if matches_info:
                    cols = {row["name"] for row in matches_info}
                    if "stage" not in cols:
                        conn.execute(text("ALTER TABLE matches ADD COLUMN stage VARCHAR(24) DEFAULT 'group'"))
                    if "round_name" not in cols:
                        conn.execute(text("ALTER TABLE matches ADD COLUMN round_name VARCHAR(40)"))
                    if "bracket_position" not in cols:
                        conn.execute(text("ALTER TABLE matches ADD COLUMN bracket_position INTEGER"))
                    if "home_team_source" not in cols:
                        conn.execute(text("ALTER TABLE matches ADD COLUMN home_team_source VARCHAR(80)"))
                    if "away_team_source" not in cols:
                        conn.execute(text("ALTER TABLE matches ADD COLUMN away_team_source VARCHAR(80)"))
                    if "winner_to_match_id" not in cols:
                        conn.execute(text("ALTER TABLE matches ADD COLUMN winner_to_match_id VARCHAR(80)"))
                    if "loser_to_match_id" not in cols:
                        conn.execute(text("ALTER TABLE matches ADD COLUMN loser_to_match_id VARCHAR(80)"))
                    if "is_placeholder_match" not in cols:
                        conn.execute(text("ALTER TABLE matches ADD COLUMN is_placeholder_match BOOLEAN DEFAULT 0"))
                    if "home_advance" not in cols:
                        conn.execute(text("ALTER TABLE matches ADD COLUMN home_advance BOOLEAN"))
                    if "away_advance" not in cols:
                        conn.execute(text("ALTER TABLE matches ADD COLUMN away_advance BOOLEAN"))
                    if "went_to_extra_time" not in cols:
                        conn.execute(text("ALTER TABLE matches ADD COLUMN went_to_extra_time BOOLEAN"))
                    if "went_to_penalties" not in cols:
                        conn.execute(text("ALTER TABLE matches ADD COLUMN went_to_penalties BOOLEAN"))

                    # Create index on stage
                    try:
                        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_matches_stage ON matches(stage)"))
                    except Exception:
                        pass  # Table may not exist yet in test scenarios

                conn.execute(text("PRAGMA user_version = 2"))

                # ai_predictions and ensemble_predictions tables will be created
                # by Base.metadata.create_all() since they're new tables

            if version < 3:
                logger.info("Upgrading database schema to version 3 (workflow system)...")

                # Create workflow_runs and workflow_steps tables
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS workflow_runs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        workflow_type VARCHAR(40) NOT NULL,
                        trigger_source VARCHAR(40) NOT NULL,
                        status VARCHAR(24) NOT NULL DEFAULT 'running',
                        started_at DATETIME NOT NULL,
                        finished_at DATETIME,
                        duration_seconds FLOAT,
                        options_json JSON,
                        summary_json JSON,
                        error_message TEXT
                    )
                """))
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS workflow_steps (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        workflow_run_id INTEGER NOT NULL REFERENCES workflow_runs(id),
                        step_name VARCHAR(60) NOT NULL,
                        status VARCHAR(24) NOT NULL DEFAULT 'pending',
                        started_at DATETIME,
                        finished_at DATETIME,
                        duration_seconds FLOAT,
                        summary_json JSON,
                        error_message TEXT
                    )
                """))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_workflow_runs_workflow_type ON workflow_runs(workflow_type)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_workflow_runs_status ON workflow_runs(status)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_workflow_steps_workflow_run_id ON workflow_steps(workflow_run_id)"))
                conn.execute(text("PRAGMA user_version = 3"))

            if version < 4:
                logger.info("Upgrading database schema to version 4 (team profile tables)...")

                # Team profile tables will be created by Base.metadata.create_all()
                # since they are defined in models.py. Here we only add indexes
                # and constraints that SQLAlchemy doesn't auto-create.

                # Add unique constraint for dedup on team_profile_predictions
                # Only if the table already exists (it may have been created by a previous
                # build_team_profiles.py run before the migration was added)
                tpp_exists = conn.scalar(text(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='team_profile_predictions'"
                ))
                if tpp_exists:
                    # Create unique index for dedup
                    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_team_profile_predictions_revision_match ON team_profile_predictions(revision_id, match_id)"))

                    # Clean up existing duplicates: keep the latest one per (revision_id, match_id)
                    try:
                        count = conn.scalar(text("SELECT COUNT(*) FROM team_profile_predictions"))
                        if count and count > 0:
                            conn.execute(text("""
                                DELETE FROM team_profile_predictions
                                WHERE id NOT IN (
                                    SELECT MAX(id) FROM team_profile_predictions
                                    GROUP BY revision_id, match_id
                                )
                            """))
                    except Exception:
                        pass  # Table may be empty or FK references not yet created

                conn.execute(text("PRAGMA user_version = 4"))

            if version < 5:
                logger.info("Upgrading database schema to version 5 (prediction_snapshots: add id PK, model_version to unique constraint)...")

                # SQLite doesn't support ALTER TABLE to change primary keys.
                # We must recreate the table.
                ps_exists = conn.scalar(text(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='prediction_snapshots'"
                ))
                if ps_exists:
                    # Check if the table already has an 'id' column (already migrated)
                    cols = conn.execute(text("PRAGMA table_info(prediction_snapshots)")).fetchall()
                    col_names = [c[1] for c in cols]

                    if "id" not in col_names:
                        # Step 1: Create new table with correct schema
                        conn.execute(text("""
                            CREATE TABLE prediction_snapshots_new (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                match_id VARCHAR NOT NULL REFERENCES matches(id),
                                revision_id INTEGER NOT NULL REFERENCES dashboard_revisions(id),
                                kickoff DATETIME,
                                is_pre_match_locked BOOLEAN DEFAULT 0,
                                is_fallback_locked BOOLEAN DEFAULT 0,
                                home_win FLOAT,
                                draw FLOAT,
                                away_win FLOAT,
                                home_xg FLOAT,
                                away_xg FLOAT,
                                has_auto_adjustments BOOLEAN DEFAULT 0,
                                base_home_win FLOAT,
                                base_draw FLOAT,
                                base_away_win FLOAT,
                                scorelines JSON,
                                score_matrix JSON,
                                confidence FLOAT,
                                confidence_label VARCHAR(16),
                                model_inputs JSON,
                                model_version VARCHAR(40),
                                snapshotted_at DATETIME
                            )
                        """))
                        # Step 2: Copy data (use 'elo-poisson-v1' as default model_version if missing)
                        conn.execute(text("""
                            INSERT INTO prediction_snapshots_new
                                (match_id, revision_id, kickoff, is_pre_match_locked, is_fallback_locked,
                                 home_win, draw, away_win, home_xg, away_xg,
                                 has_auto_adjustments, base_home_win, base_draw, base_away_win,
                                 scorelines, score_matrix, confidence, confidence_label,
                                 model_inputs, model_version, snapshotted_at)
                            SELECT
                                match_id, revision_id, kickoff, is_pre_match_locked, is_fallback_locked,
                                 home_win, draw, away_win, home_xg, away_xg,
                                 has_auto_adjustments, base_home_win, base_draw, base_away_win,
                                 scorelines, score_matrix, confidence, confidence_label,
                                 model_inputs,
                                 COALESCE(model_version, 'elo-poisson-v1'),
                                 snapshotted_at
                            FROM prediction_snapshots
                        """))
                        # Step 3: Drop old table and rename
                        conn.execute(text("DROP TABLE prediction_snapshots"))
                        conn.execute(text("ALTER TABLE prediction_snapshots_new RENAME TO prediction_snapshots"))
                        # Step 4: Recreate indexes
                        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_prediction_snapshots_match_id ON prediction_snapshots(match_id)"))
                        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_prediction_snapshots_revision_id ON prediction_snapshots(revision_id)"))
                        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_prediction_snapshots_kickoff ON prediction_snapshots(kickoff)"))
                        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_prediction_snapshots_is_pre_match_locked ON prediction_snapshots(is_pre_match_locked)"))
                        # Step 5: Add unique constraint
                        conn.execute(text("""
                            CREATE UNIQUE INDEX IF NOT EXISTS uq_prediction_snapshot_match_revision_version
                            ON prediction_snapshots(match_id, revision_id, model_version)
                        """))

                conn.execute(text("PRAGMA user_version = 5"))

            if version < 6:
                logger.info("Upgrading database schema to version 6 (ensemble_predictions: add is_fallback_locked, real_time_only)...")

                # Add new columns to ensemble_predictions
                ep_exists = conn.scalar(text(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='ensemble_predictions'"
                ))
                if ep_exists:
                    ep_info = conn.execute(text("PRAGMA table_info(ensemble_predictions)")).mappings().all()
                    ep_cols = {row["name"] for row in ep_info}
                    if "is_fallback_locked" not in ep_cols:
                        conn.execute(text("ALTER TABLE ensemble_predictions ADD COLUMN is_fallback_locked BOOLEAN NOT NULL DEFAULT 0"))
                    if "real_time_only" not in ep_cols:
                        conn.execute(text("ALTER TABLE ensemble_predictions ADD COLUMN real_time_only BOOLEAN NOT NULL DEFAULT 0"))

                conn.execute(text("PRAGMA user_version = 6"))

            if version < 7:
                logger.info("Upgrading database schema to version 7 (ensemble_predictions: add source_ids_json)...")
                ep7_exists = conn.scalar(text(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='ensemble_predictions'"
                ))
                if ep7_exists:
                    ep7_info = conn.execute(text("PRAGMA table_info(ensemble_predictions)")).mappings().all()
                    ep7_cols = {row["name"] for row in ep7_info}
                    if "source_ids_json" not in ep7_cols:
                        conn.execute(text("ALTER TABLE ensemble_predictions ADD COLUMN source_ids_json JSON"))

                conn.execute(text("PRAGMA user_version = 7"))

            if version < 8:
                logger.info("Upgrading database schema to version 8 (ensemble lock dedup + unique constraint)...")
                ep8_exists = conn.scalar(text(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='ensemble_predictions'"
                ))
                if ep8_exists:
                    # Step 1: Fix duplicate official locked records
                    # Keep the earliest locked record, demote others to unlocked
                    dup_locked = conn.execute(text("""
                        SELECT match_id, model_version, COUNT(*) as cnt
                        FROM ensemble_predictions
                        WHERE is_pre_match_locked = 1
                        GROUP BY match_id, model_version
                        HAVING cnt > 1
                    """)).fetchall()
                    for match_id, model_version, cnt in dup_locked:
                        # Get the earliest locked record (the one to keep)
                        keep_id = conn.scalar(text("""
                            SELECT id FROM ensemble_predictions
                            WHERE match_id = :mid AND model_version = :mv AND is_pre_match_locked = 1
                            ORDER BY locked_at ASC, created_at ASC
                            LIMIT 1
                        """), {"mid": match_id, "mv": model_version})
                        # Demote all other locked records for this match+version
                        conn.execute(text("""
                            UPDATE ensemble_predictions
                            SET is_pre_match_locked = 0, locked_at = NULL
                            WHERE match_id = :mid AND model_version = :mv
                              AND is_pre_match_locked = 1 AND id != :keep_id
                        """), {"mid": match_id, "mv": model_version, "keep_id": keep_id})
                        logger.info(f"  Deduped {cnt-1} duplicate locked ensembles for {match_id}/{model_version}")

                    # Step 2: Fix duplicate fallback locked records
                    dup_fallback = conn.execute(text("""
                        SELECT match_id, model_version, COUNT(*) as cnt
                        FROM ensemble_predictions
                        WHERE is_fallback_locked = 1
                        GROUP BY match_id, model_version
                        HAVING cnt > 1
                    """)).fetchall()
                    for match_id, model_version, cnt in dup_fallback:
                        keep_id = conn.scalar(text("""
                            SELECT id FROM ensemble_predictions
                            WHERE match_id = :mid AND model_version = :mv AND is_fallback_locked = 1
                            ORDER BY created_at DESC
                            LIMIT 1
                        """), {"mid": match_id, "mv": model_version})
                        conn.execute(text("""
                            UPDATE ensemble_predictions
                            SET is_fallback_locked = 0
                            WHERE match_id = :mid AND model_version = :mv
                              AND is_fallback_locked = 1 AND id != :keep_id
                        """), {"mid": match_id, "mv": model_version, "keep_id": keep_id})
                        logger.info(f"  Deduped {cnt-1} duplicate fallback ensembles for {match_id}/{model_version}")

                    # Step 3: Add unique partial index for locked ensembles
                    # SQLite doesn't support partial unique indexes via CREATE UNIQUE INDEX WHERE,
                    # so we use a trigger approach instead
                    # Create a tracking table for lock enforcement
                    conn.execute(text("""
                        CREATE TABLE IF NOT EXISTS ensemble_lock_tracker (
                            match_id VARCHAR NOT NULL,
                            model_version VARCHAR NOT NULL,
                            lock_type VARCHAR NOT NULL,
                            ensemble_id INTEGER NOT NULL,
                            PRIMARY KEY (match_id, model_version, lock_type)
                        )
                    """))
                    # Populate tracker from existing locked records
                    conn.execute(text("DELETE FROM ensemble_lock_tracker"))
                    conn.execute(text("""
                        INSERT INTO ensemble_lock_tracker (match_id, model_version, lock_type, ensemble_id)
                        SELECT match_id, model_version, 'official', MIN(id)
                        FROM ensemble_predictions
                        WHERE is_pre_match_locked = 1
                        GROUP BY match_id, model_version
                    """))
                    conn.execute(text("""
                        INSERT OR IGNORE INTO ensemble_lock_tracker (match_id, model_version, lock_type, ensemble_id)
                        SELECT match_id, model_version, 'fallback', MIN(id)
                        FROM ensemble_predictions
                        WHERE is_fallback_locked = 1
                        GROUP BY match_id, model_version
                    """))

                conn.execute(text("PRAGMA user_version = 8"))

            if version < 9:
                logger.info("Upgrading database schema to version 9 (historical_matches v2)...")
                hm_exists = conn.scalar(text(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='historical_matches'"
                ))
                if hm_exists:
                    # Check if the table has the old schema (has 'played_on' column)
                    hm_info = conn.execute(text("PRAGMA table_info(historical_matches)")).fetchall()
                    hm_cols = [c[1] for c in hm_info]
                    if "played_on" in hm_cols:
                        # Old schema - drop and recreate
                        conn.execute(text("DROP TABLE historical_matches"))
                        hm_exists = 0

                if not hm_exists:
                    conn.execute(text("""
                        CREATE TABLE historical_matches (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            source_match_id VARCHAR(120) NOT NULL,
                            provider VARCHAR(40) NOT NULL,
                            kickoff DATETIME NOT NULL,
                            home_team_id VARCHAR(32) REFERENCES teams(id),
                            away_team_id VARCHAR(32) REFERENCES teams(id),
                            home_team_raw VARCHAR(120) NOT NULL,
                            away_team_raw VARCHAR(120) NOT NULL,
                            home_score INTEGER NOT NULL,
                            away_score INTEGER NOT NULL,
                            home_score_90min INTEGER,
                            away_score_90min INTEGER,
                            neutral_venue BOOLEAN DEFAULT 1,
                            competition VARCHAR(160) NOT NULL,
                            competition_type VARCHAR(40) NOT NULL,
                            match_importance FLOAT DEFAULT 1.0,
                            went_to_extra_time BOOLEAN DEFAULT 0,
                            went_to_penalties BOOLEAN DEFAULT 0,
                            penalty_winner VARCHAR(120),
                            city VARCHAR(120),
                            country VARCHAR(120),
                            fetched_at DATETIME NOT NULL,
                            source_updated_at DATETIME,
                            raw_payload JSON,
                            data_version INTEGER DEFAULT 1,
                            is_unmapped BOOLEAN DEFAULT 0
                        )
                    """))
                    # Create indexes
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_historical_matches_source_match_id ON historical_matches(source_match_id)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_historical_matches_provider ON historical_matches(provider)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_historical_matches_kickoff ON historical_matches(kickoff)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_historical_matches_home_team_id ON historical_matches(home_team_id)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_historical_matches_away_team_id ON historical_matches(away_team_id)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_historical_matches_home_team_raw ON historical_matches(home_team_raw)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_historical_matches_away_team_raw ON historical_matches(away_team_raw)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_historical_matches_competition ON historical_matches(competition)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_historical_matches_competition_type ON historical_matches(competition_type)"))
                    # Create unique constraint
                    conn.execute(text("""
                        CREATE UNIQUE INDEX IF NOT EXISTS uq_historical_match_provider_source
                        ON historical_matches(provider, source_match_id)
                    """))

                conn.execute(text("PRAGMA user_version = 9"))

            if version < 10:
                logger.info("Upgrading database schema to version 10 (historical_teams + historical_matches FK removal)...")

                # Step 1: Create historical_teams table
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS historical_teams (
                        id VARCHAR(32) PRIMARY KEY,
                        name VARCHAR(120) NOT NULL,
                        provider VARCHAR(40) NOT NULL,
                        provider_team_id VARCHAR(120) NOT NULL,
                        team_category VARCHAR(40) NOT NULL,
                        current_team_id VARCHAR(32) REFERENCES teams(id),
                        former_name_of VARCHAR(32),
                        aliases JSON,
                        is_active BOOLEAN DEFAULT 1,
                        created_at DATETIME
                    )
                """))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_historical_teams_name ON historical_teams(name)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_historical_teams_provider ON historical_teams(provider)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_historical_teams_provider_team_id ON historical_teams(provider_team_id)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_historical_teams_team_category ON historical_teams(team_category)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_historical_teams_current_team_id ON historical_teams(current_team_id)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_historical_teams_former_name_of ON historical_teams(former_name_of)"))
                conn.execute(text("""
                    CREATE UNIQUE INDEX IF NOT EXISTS uq_historical_team_provider_id
                    ON historical_teams(provider, provider_team_id)
                """))

                # Step 2: Recreate historical_matches table without FK constraints
                # and with new home_team_source / away_team_source columns
                hm10_exists = conn.scalar(text(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='historical_matches'"
                ))
                if hm10_exists:
                    # Check if already migrated (has home_team_source column)
                    hm10_info = conn.execute(text("PRAGMA table_info(historical_matches)")).fetchall()
                    hm10_cols = [c[1] for c in hm10_info]

                    if "home_team_source" not in hm10_cols:
                        # Need to recreate the table
                        conn.execute(text("""
                            CREATE TABLE historical_matches_new (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                source_match_id VARCHAR(120) NOT NULL,
                                provider VARCHAR(40) NOT NULL,
                                kickoff DATETIME NOT NULL,
                                home_team_id VARCHAR(32),
                                away_team_id VARCHAR(32),
                                home_team_source VARCHAR(20) NOT NULL DEFAULT 'world_cup',
                                away_team_source VARCHAR(20) NOT NULL DEFAULT 'world_cup',
                                home_team_raw VARCHAR(120) NOT NULL,
                                away_team_raw VARCHAR(120) NOT NULL,
                                home_score INTEGER NOT NULL,
                                away_score INTEGER NOT NULL,
                                home_score_90min INTEGER,
                                away_score_90min INTEGER,
                                neutral_venue BOOLEAN DEFAULT 1,
                                competition VARCHAR(160) NOT NULL,
                                competition_type VARCHAR(40) NOT NULL,
                                match_importance FLOAT DEFAULT 1.0,
                                went_to_extra_time BOOLEAN DEFAULT 0,
                                went_to_penalties BOOLEAN DEFAULT 0,
                                penalty_winner VARCHAR(120),
                                city VARCHAR(120),
                                country VARCHAR(120),
                                fetched_at DATETIME NOT NULL,
                                source_updated_at DATETIME,
                                raw_payload JSON,
                                data_version INTEGER DEFAULT 1,
                                is_unmapped BOOLEAN DEFAULT 0
                            )
                        """))
                        # Copy data - set home_team_source/away_team_source based on whether team_id exists in teams table
                        conn.execute(text("""
                            INSERT INTO historical_matches_new (
                                id, source_match_id, provider, kickoff,
                                home_team_id, away_team_id,
                                home_team_source, away_team_source,
                                home_team_raw, away_team_raw,
                                home_score, away_score,
                                home_score_90min, away_score_90min,
                                neutral_venue, competition, competition_type, match_importance,
                                went_to_extra_time, went_to_penalties, penalty_winner,
                                city, country, fetched_at, source_updated_at,
                                raw_payload, data_version, is_unmapped
                            )
                            SELECT
                                id, source_match_id, provider, kickoff,
                                home_team_id, away_team_id,
                                CASE WHEN home_team_id IS NOT NULL THEN 'world_cup' ELSE 'unknown' END,
                                CASE WHEN away_team_id IS NOT NULL THEN 'world_cup' ELSE 'unknown' END,
                                home_team_raw, away_team_raw,
                                home_score, away_score,
                                home_score_90min, away_score_90min,
                                neutral_venue, competition, competition_type, match_importance,
                                went_to_extra_time, went_to_penalties, penalty_winner,
                                city, country, fetched_at, source_updated_at,
                                raw_payload, data_version, is_unmapped
                            FROM historical_matches
                        """))
                        conn.execute(text("DROP TABLE historical_matches"))
                        conn.execute(text("ALTER TABLE historical_matches_new RENAME TO historical_matches"))
                        # Recreate indexes
                        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_historical_matches_source_match_id ON historical_matches(source_match_id)"))
                        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_historical_matches_provider ON historical_matches(provider)"))
                        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_historical_matches_kickoff ON historical_matches(kickoff)"))
                        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_historical_matches_home_team_id ON historical_matches(home_team_id)"))
                        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_historical_matches_away_team_id ON historical_matches(away_team_id)"))
                        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_historical_matches_home_team_raw ON historical_matches(home_team_raw)"))
                        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_historical_matches_away_team_raw ON historical_matches(away_team_raw)"))
                        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_historical_matches_competition ON historical_matches(competition)"))
                        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_historical_matches_competition_type ON historical_matches(competition_type)"))
                        conn.execute(text("""
                            CREATE UNIQUE INDEX IF NOT EXISTS uq_historical_match_provider_source
                            ON historical_matches(provider, source_match_id)
                        """))

                conn.execute(text("PRAGMA user_version = 10"))

            if version < 11:
                logger.info("Upgrading database schema to version 11 (time_precision, available_at, score_scope)...")
                hm11_exists = conn.scalar(text(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='historical_matches'"
                ))
                if hm11_exists:
                    hm11_info = conn.execute(text("PRAGMA table_info(historical_matches)")).mappings().all()
                    hm11_cols = {row["name"] for row in hm11_info}

                    if "time_precision" not in hm11_cols:
                        conn.execute(text("ALTER TABLE historical_matches ADD COLUMN time_precision VARCHAR(20) NOT NULL DEFAULT 'exact'"))
                    if "available_at" not in hm11_cols:
                        conn.execute(text("ALTER TABLE historical_matches ADD COLUMN available_at DATETIME"))
                    if "score_scope" not in hm11_cols:
                        conn.execute(text("ALTER TABLE historical_matches ADD COLUMN score_scope VARCHAR(40) NOT NULL DEFAULT 'full_90min'"))

                    # Update existing records: set time_precision='date_only', available_at=kickoff+1day
                    conn.execute(text("""
                        UPDATE historical_matches
                        SET time_precision = 'date_only',
                            available_at = DATETIME(kickoff, '+1 day')
                        WHERE available_at IS NULL
                    """))

                    # Update existing records: set score_scope='after_extra_time_or_unknown'
                    # where went_to_extra_time=1 or went_to_penalties=1
                    conn.execute(text("""
                        UPDATE historical_matches
                        SET score_scope = 'after_extra_time_or_unknown'
                        WHERE (went_to_extra_time = 1 OR went_to_penalties = 1)
                          AND score_scope = 'full_90min'
                    """))

                    # Create index on available_at
                    try:
                        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_historical_matches_available_at ON historical_matches(available_at)"))
                    except Exception:
                        pass

                conn.execute(text("PRAGMA user_version = 11"))

            if version < 12:
                logger.info("Upgrading database schema to version 12 (backtest_results table)...")

                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS backtest_results (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        data_version VARCHAR(40) NOT NULL,
                        model_name VARCHAR(80) NOT NULL,
                        split_name VARCHAR(20) NOT NULL,
                        brier_score FLOAT NOT NULL,
                        brier_score_avg FLOAT NOT NULL,
                        log_loss FLOAT NOT NULL,
                        ece FLOAT NOT NULL,
                        top1_hit_rate FLOAT NOT NULL,
                        draw_recall FLOAT NOT NULL,
                        match_count INTEGER NOT NULL,
                        parameters_json JSON,
                        stratified_json JSON,
                        admission_status VARCHAR(20) NOT NULL DEFAULT 'pending',
                        created_at DATETIME
                    )
                """))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_backtest_results_data_version ON backtest_results(data_version)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_backtest_results_model_name ON backtest_results(model_name)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_backtest_results_split_name ON backtest_results(split_name)"))

                conn.execute(text("PRAGMA user_version = 12"))

        except Exception as e:
            logger.error(f"Failed to upgrade database schema: {e}")
            raise



def create_database(path: str | Path | None = None) -> Engine:
    global _engine, _session_factory

    database_path = Path(path or settings.database_path).expanduser().resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )
    _configure_sqlite(engine)
    _upgrade_schema(engine)
    Base.metadata.create_all(engine)
    _engine = engine
    _session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    return engine


def get_engine() -> Engine:
    if _engine is None:
        return create_database()
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    global _session_factory

    if _session_factory is None:
        create_database()
    assert _session_factory is not None
    session = _session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

