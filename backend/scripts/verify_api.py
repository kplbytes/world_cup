#!/usr/bin/env python3
"""API verification script for the data pipeline fix."""
import json
from app.db import create_database, session_scope
from app.services.scoring import audit_data_integrity, get_match_count_breakdown
from app.team_profiles.service import get_profile_coverage

create_database()
with session_scope() as session:
    # 1. Data integrity audit
    audit = audit_data_integrity(session)
    print("=== DATA INTEGRITY AUDIT ===")
    print(json.dumps(audit["summary"], indent=2, default=str))

    # 2. Match count breakdown
    breakdown = get_match_count_breakdown(session)
    print()
    print("=== MATCH COUNT BREAKDOWN ===")
    print(f"total_finished: {breakdown.total_finished}")
    print(f"has_pre_match_prediction: {breakdown.has_pre_match_prediction}")
    print(f"has_pre_kickoff_snapshot: {breakdown.has_pre_kickoff_snapshot}")
    print(f"actually_scored: {breakdown.actually_scored}")
    print(f"data_gap_no_snapshot: {breakdown.data_gap_no_snapshot}")
    print(f"sample_size: {breakdown.sample_size}")

    # 3. Profile coverage
    coverage = get_profile_coverage(session)
    print()
    print("=== PROFILE COVERAGE ===")
    print(json.dumps(coverage, indent=2, default=str))
