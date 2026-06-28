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
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


def _ensure_supporting_indexes(engine: Engine) -> None:
    """Create lightweight read-path indexes that should exist on every database."""
    with engine.begin() as conn:
        prediction_snapshots_exists = conn.scalar(text(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='prediction_snapshots'"
        ))
        if prediction_snapshots_exists:
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_prediction_snapshots_match_id_snapshotted_at
                ON prediction_snapshots(match_id, snapshotted_at DESC)
            """))


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
                    if "home_penalty_score" not in cols:
                        conn.execute(text("ALTER TABLE matches ADD COLUMN home_penalty_score INTEGER"))
                    if "away_penalty_score" not in cols:
                        conn.execute(text("ALTER TABLE matches ADD COLUMN away_penalty_score INTEGER"))

                    # Create index on stage
                    try:
                        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_matches_stage ON matches(stage)"))
                    except Exception as e:
                        logger.warning(f"Index creation skipped: {e}")

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
                    except Exception as e:
                        logger.warning(f"Cleanup skipped: {e}")

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
                logger.info("Upgrading database schema to version 6 (structured team profile modules)...")

                profiles_info = conn.execute(text("PRAGMA table_info(team_profiles)")).mappings().all()
                if profiles_info:
                    cols = {row["name"] for row in profiles_info}
                    profile_columns = {
                        "long_term_strength_score": "FLOAT NOT NULL DEFAULT 0",
                        "recent_form_score": "FLOAT NOT NULL DEFAULT 0",
                        "attack_score": "FLOAT NOT NULL DEFAULT 0",
                        "defense_score": "FLOAT NOT NULL DEFAULT 0",
                        "stability_score": "FLOAT NOT NULL DEFAULT 0",
                        "tournament_experience_score": "FLOAT NOT NULL DEFAULT 0",
                        "lineup_integrity_score": "FLOAT",
                        "injury_risk_score": "FLOAT",
                        "rest_days": "INTEGER",
                        "schedule_fatigue_score": "FLOAT",
                        "environment_adaptation_score": "FLOAT",
                        "data_quality_score": "FLOAT NOT NULL DEFAULT 0",
                        "tactical_style_tags_json": "JSON DEFAULT '[]'",
                        "strong_opponent_performance_json": "JSON DEFAULT '{}'",
                        "middle_opponent_performance_json": "JSON DEFAULT '{}'",
                        "weak_opponent_performance_json": "JSON DEFAULT '{}'",
                        "strengths_json": "JSON DEFAULT '[]'",
                        "weaknesses_json": "JSON DEFAULT '[]'",
                        "risk_flags_json": "JSON DEFAULT '[]'",
                        "missing_fields_json": "JSON DEFAULT '[]'",
                        "source_list_json": "JSON DEFAULT '[]'",
                        "narrative_json": "JSON DEFAULT '{}'",
                        "data_quality_json": "JSON DEFAULT '{}'",
                        "profile_modules_json": "JSON DEFAULT '{}'",
                        "usage_scope": "VARCHAR(32) NOT NULL DEFAULT 'display_only'",
                        "prediction_enabled": "BOOLEAN NOT NULL DEFAULT 0",
                    }
                    for column, ddl in profile_columns.items():
                        if column not in cols:
                            conn.execute(text(f"ALTER TABLE team_profiles ADD COLUMN {column} {ddl}"))

                conn.execute(text("PRAGMA user_version = 6"))

            if version < 7:
                logger.info("Upgrading database schema to version 7 (prediction snapshot access indexes)...")
                prediction_snapshots_exists = conn.scalar(text(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='prediction_snapshots'"
                ))
                if prediction_snapshots_exists:
                    conn.execute(text("""
                        CREATE INDEX IF NOT EXISTS ix_prediction_snapshots_match_id_snapshotted_at
                        ON prediction_snapshots(match_id, snapshotted_at DESC)
                    """))
                conn.execute(text("PRAGMA user_version = 7"))

            if version < 8:
                logger.info("Upgrading database schema to version 8 (penalty shootout score columns)...")
                # P1-8: home_penalty_score / away_penalty_score on matches.
                # Placed after v7 so migration order is strictly increasing.
                # v2 block already adds these columns for fresh DBs; this block
                # ensures existing DBs that ran v2 before P1-8 also get the columns.
                matches_info = conn.execute(text("PRAGMA table_info(matches)")).mappings().all()
                if matches_info:
                    cols = {row["name"] for row in matches_info}
                    if "home_penalty_score" not in cols:
                        conn.execute(text("ALTER TABLE matches ADD COLUMN home_penalty_score INTEGER"))
                    if "away_penalty_score" not in cols:
                        conn.execute(text("ALTER TABLE matches ADD COLUMN away_penalty_score INTEGER"))
                conn.execute(text("PRAGMA user_version = 8"))

            if version < 9:
                logger.info("Upgrading database schema to version 9 (composite indexes for dashboard performance)...")
                # Add composite indexes to speed up dashboard queries that filter
                # by revision_id + model_version, match_id + created_at, etc.
                # These are safe to CREATE IF NOT EXISTS.
                for idx_sql in [
                    "CREATE INDEX IF NOT EXISTS ix_match_predictions_revision_model ON match_predictions(revision_id, model_version)",
                    "CREATE INDEX IF NOT EXISTS ix_match_predictions_match_id ON match_predictions(match_id)",
                    "CREATE INDEX IF NOT EXISTS ix_market_snapshots_provider_match ON market_snapshots(provider, match_id)",
                    "CREATE INDEX IF NOT EXISTS ix_ai_predictions_match_created ON ai_predictions(match_id, created_at DESC)",
                    "CREATE INDEX IF NOT EXISTS ix_ensemble_predictions_match_created ON ensemble_predictions(match_id, created_at DESC)",
                    "CREATE INDEX IF NOT EXISTS ix_auto_adjustments_match_id ON auto_adjustments(match_id)",
                ]:
                    try:
                        conn.execute(text(idx_sql))
                    except Exception as e:
                        logger.warning(f"Index creation skipped: {e}")
                conn.execute(text("PRAGMA user_version = 9"))

        except Exception as e:
            logger.error(f"Failed to upgrade database schema: {e}")
            raise



def create_database(path: str | Path | None = None) -> Engine:
    global _engine, _session_factory

    if _engine is not None:
        _engine.dispose()
        _session_factory = None

    database_path = Path(path or settings.database_path).expanduser().resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        pool_size=20,
        max_overflow=20,
        pool_timeout=60,
        pool_pre_ping=True,
    )
    _configure_sqlite(engine)
    _upgrade_schema(engine)
    Base.metadata.create_all(engine)
    _ensure_supporting_indexes(engine)
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
