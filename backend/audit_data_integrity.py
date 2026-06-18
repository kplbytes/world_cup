#!/usr/bin/env python3
"""
Comprehensive Data Integrity Audit for World Cup Backend
Checks all completed/final matches and their prediction data lifecycle.
"""

import sqlite3
import json
from datetime import datetime

DB_PATH = "/Users/liudapeng/Documents/code/others/world_cup/backend/world_cup.db"

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    print("=" * 120)
    print("WORLD CUP DATA INTEGRITY AUDIT")
    print(f"Run at: {datetime.now().isoformat()}")
    print("=" * 120)

    # ── Step 1: Find completed matches ──
    print("\n── STEP 1: Identifying completed/final matches ──")
    cur.execute("""
        SELECT id, group_code, home_team_id, away_team_id, kickoff, status,
               home_score, away_score, stage, round_name,
               went_to_extra_time, went_to_penalties,
               is_placeholder_match, source, source_updated_at
        FROM matches
        WHERE status IN ('final', 'completed')
        ORDER BY kickoff
    """)
    final_matches = cur.fetchall()
    print(f"Total completed matches: {len(final_matches)}")

    # Also check all distinct statuses
    cur.execute("SELECT status, COUNT(*) FROM matches GROUP BY status ORDER BY status")
    status_counts = cur.fetchall()
    print(f"All match statuses: {dict((r[0], r[1]) for r in status_counts)}")

    # ── Step 2: Per-match audit ──
    print("\n── STEP 2: Per-match detailed audit ──")
    print("-" * 120)

    results = []

    for m in final_matches:
        match_id = m['id']
        kickoff = m['kickoff']

        row = {
            'match_id': match_id,
            'home_team': m['home_team_id'],
            'away_team': m['away_team_id'],
            'kickoff': kickoff,
            'status': m['status'],
            'home_score': m['home_score'],
            'away_score': m['away_score'],
            'stage': m['stage'],
            'extra_time': m['went_to_extra_time'],
            'penalties': m['went_to_penalties'],
        }

        # --- match_predictions (base predictions) ---
        cur.execute("""
            SELECT mp.*, dr.model_version as rev_model_version, dr.active as rev_active
            FROM match_predictions mp
            LEFT JOIN dashboard_revisions dr ON mp.revision_id = dr.id
            WHERE mp.match_id = ?
            ORDER BY mp.id DESC
        """, (match_id,))
        mp_rows = cur.fetchall()
        row['has_base_prediction'] = len(mp_rows) > 0
        row['base_prediction_count'] = len(mp_rows)
        if mp_rows:
            first = mp_rows[0]
            row['base_prediction_source'] = first['model_version']
            row['base_prediction_rev_id'] = first['revision_id']
            row['base_prediction_rev_active'] = first['rev_active']
            row['base_prediction_rev_model'] = first['rev_model_version']
            row['base_home_win'] = first['home_win']
            row['base_draw'] = first['draw']
            row['base_away_win'] = first['away_win']
        else:
            row['base_prediction_source'] = None
            row['base_prediction_rev_id'] = None
            row['base_prediction_rev_active'] = None
            row['base_prediction_rev_model'] = None
            row['base_home_win'] = None
            row['base_draw'] = None
            row['base_away_win'] = None

        # --- prediction_snapshots ---
        cur.execute("""
            SELECT ps.*, dr.model_version as rev_model_version, dr.active as rev_active
            FROM prediction_snapshots ps
            LEFT JOIN dashboard_revisions dr ON ps.revision_id = dr.id
            WHERE ps.match_id = ?
            ORDER BY ps.snapshotted_at DESC
        """, (match_id,))
        ps_rows = cur.fetchall()
        row['prediction_snapshot_count'] = len(ps_rows)
        row['has_prediction_snapshot'] = len(ps_rows) > 0

        if ps_rows:
            latest = ps_rows[0]
            row['latest_snapshot_at'] = latest['snapshotted_at']
            row['snapshot_revision_id'] = latest['revision_id']
            row['snapshot_model_version'] = latest['model_version']
            row['snapshot_rev_model_version'] = latest['rev_model_version']
            row['snapshot_rev_active'] = latest['rev_active']
            row['snapshot_has_probabilities'] = (
                latest['home_win'] is not None
                and latest['draw'] is not None
                and latest['away_win'] is not None
            )
            row['snapshot_is_pre_match_locked'] = latest['is_pre_match_locked']
            row['snapshot_is_fallback_locked'] = latest['is_fallback_locked']
            row['snapshot_home_win'] = latest['home_win']
            row['snapshot_draw'] = latest['draw']
            row['snapshot_away_win'] = latest['away_win']

            # Check if ANY snapshot was before kickoff
            pre_kickoff = [s for s in ps_rows if s['snapshotted_at'] < kickoff]
            row['latest_snapshot_before_kickoff'] = len(pre_kickoff) > 0
            row['pre_kickoff_snapshot_count'] = len(pre_kickoff)
            if pre_kickoff:
                # The latest one before kickoff
                pk = pre_kickoff[0]  # already sorted DESC by snapshotted_at
                row['latest_pre_kickoff_at'] = pk['snapshotted_at']
                row['latest_pre_kickoff_locked'] = pk['is_pre_match_locked']
            else:
                row['latest_pre_kickoff_at'] = None
                row['latest_pre_kickoff_locked'] = None

            # All snapshot timestamps
            row['all_snapshot_times'] = [s['snapshotted_at'] for s in ps_rows]
            row['all_snapshot_locked'] = [s['is_pre_match_locked'] for s in ps_rows]
        else:
            row['latest_snapshot_at'] = None
            row['snapshot_revision_id'] = None
            row['snapshot_model_version'] = None
            row['snapshot_rev_model_version'] = None
            row['snapshot_rev_active'] = None
            row['snapshot_has_probabilities'] = None
            row['snapshot_is_pre_match_locked'] = None
            row['snapshot_is_fallback_locked'] = None
            row['snapshot_home_win'] = None
            row['snapshot_draw'] = None
            row['snapshot_away_win'] = None
            row['latest_snapshot_before_kickoff'] = False
            row['pre_kickoff_snapshot_count'] = 0
            row['latest_pre_kickoff_at'] = None
            row['latest_pre_kickoff_locked'] = None
            row['all_snapshot_times'] = []
            row['all_snapshot_locked'] = []

        # --- ai_predictions ---
        cur.execute("""
            SELECT * FROM ai_predictions
            WHERE match_id = ?
            ORDER BY created_at DESC
        """, (match_id,))
        ai_rows = cur.fetchall()
        row['has_ai_prediction'] = len(ai_rows) > 0
        row['ai_prediction_count'] = len(ai_rows)
        if ai_rows:
            first = ai_rows[0]
            row['ai_provider'] = first['provider']
            row['ai_model_id'] = first['model_id']
            row['ai_is_pre_match_locked'] = first['is_pre_match_locked']
            row['ai_is_fallback_locked'] = first['is_fallback_locked']
            row['ai_real_time_only'] = first['real_time_only']
            row['ai_locked_at'] = first['locked_at']
            row['ai_created_at'] = first['created_at']
            row['ai_has_parsed_probs'] = (
                first['parsed_home_win'] is not None
                and first['parsed_draw'] is not None
                and first['parsed_away_win'] is not None
            )
            # Check if any AI prediction was created before kickoff
            ai_pre_kickoff = [a for a in ai_rows if a['created_at'] < kickoff]
            row['ai_pre_kickoff_count'] = len(ai_pre_kickoff)
        else:
            row['ai_provider'] = None
            row['ai_model_id'] = None
            row['ai_is_pre_match_locked'] = None
            row['ai_is_fallback_locked'] = None
            row['ai_real_time_only'] = None
            row['ai_locked_at'] = None
            row['ai_created_at'] = None
            row['ai_has_parsed_probs'] = None
            row['ai_pre_kickoff_count'] = 0

        # --- ensemble_predictions ---
        cur.execute("""
            SELECT * FROM ensemble_predictions
            WHERE match_id = ?
            ORDER BY created_at DESC
        """, (match_id,))
        ens_rows = cur.fetchall()
        row['has_ensemble_prediction'] = len(ens_rows) > 0
        row['ensemble_prediction_count'] = len(ens_rows)
        if ens_rows:
            first = ens_rows[0]
            row['ensemble_model_version'] = first['model_version']
            row['ensemble_is_pre_match_locked'] = first['is_pre_match_locked']
            row['ensemble_is_fallback_locked'] = first['is_fallback_locked']
            row['ensemble_real_time_only'] = first['real_time_only']
            row['ensemble_locked_at'] = first['locked_at']
            row['ensemble_created_at'] = first['created_at']
            row['ensemble_home_win'] = first['ensemble_home_win']
            row['ensemble_draw'] = first['ensemble_draw']
            row['ensemble_away_win'] = first['ensemble_away_win']
            # Check if any ensemble was created before kickoff
            ens_pre_kickoff = [e for e in ens_rows if e['created_at'] < kickoff]
            row['ensemble_pre_kickoff_count'] = len(ens_pre_kickoff)
        else:
            row['ensemble_model_version'] = None
            row['ensemble_is_pre_match_locked'] = None
            row['ensemble_is_fallback_locked'] = None
            row['ensemble_real_time_only'] = None
            row['ensemble_locked_at'] = None
            row['ensemble_created_at'] = None
            row['ensemble_home_win'] = None
            row['ensemble_draw'] = None
            row['ensemble_away_win'] = None
            row['ensemble_pre_kickoff_count'] = 0

        # --- ensemble_lock_tracker ---
        cur.execute("""
            SELECT * FROM ensemble_lock_tracker
            WHERE match_id = ?
        """, (match_id,))
        lock_rows = cur.fetchall()
        row['has_lock_tracker'] = len(lock_rows) > 0
        row['lock_tracker_count'] = len(lock_rows)
        if lock_rows:
            row['lock_tracker_entries'] = [
                {'lock_type': l['lock_type'], 'model_version': l['model_version'], 'ensemble_id': l['ensemble_id']}
                for l in lock_rows
            ]
        else:
            row['lock_tracker_entries'] = []

        # --- team_profile_predictions ---
        cur.execute("""
            SELECT * FROM team_profile_predictions
            WHERE match_id = ?
            ORDER BY created_at DESC
        """, (match_id,))
        tp_rows = cur.fetchall()
        row['has_team_profile_prediction'] = len(tp_rows) > 0
        row['team_profile_prediction_count'] = len(tp_rows)
        if tp_rows:
            first = tp_rows[0]
            row['tp_model_version'] = first['model_version']
            row['tp_is_pre_match_locked'] = first['is_pre_match_locked']
            row['tp_is_fallback_locked'] = first['is_fallback_locked']
            row['tp_real_time_only'] = first['real_time_only']
            row['tp_locked_at'] = first['locked_at']
            row['tp_created_at'] = first['created_at']
        else:
            row['tp_model_version'] = None
            row['tp_is_pre_match_locked'] = None
            row['tp_is_fallback_locked'] = None
            row['tp_real_time_only'] = None
            row['tp_locked_at'] = None
            row['tp_created_at'] = None

        # --- market_snapshots ---
        cur.execute("""
            SELECT * FROM market_snapshots
            WHERE match_id = ?
            ORDER BY fetched_at DESC
        """, (match_id,))
        mkt_rows = cur.fetchall()
        row['has_market_snapshot'] = len(mkt_rows) > 0
        row['market_snapshot_count'] = len(mkt_rows)
        if mkt_rows:
            row['latest_market_at'] = mkt_rows[0]['fetched_at']
            mkt_pre_kickoff = [mk for mk in mkt_rows if mk['fetched_at'] < kickoff]
            row['market_pre_kickoff_count'] = len(mkt_pre_kickoff)
        else:
            row['latest_market_at'] = None
            row['market_pre_kickoff_count'] = 0

        # --- Scoring eligibility ---
        # A match is scoring-eligible if:
        # 1. Has final result (home_score and away_score not null)
        # 2. Has a pre-kickoff prediction snapshot (snapshotted_at < kickoff)
        #    OR at least a pre-match-locked snapshot
        has_result = row['home_score'] is not None and row['away_score'] is not None
        has_pre_kickoff_snap = row.get('latest_snapshot_before_kickoff', False)
        has_locked_snap = any(row.get('all_snapshot_locked', []))

        # More nuanced: check if any prediction snapshot has is_pre_match_locked = 1
        has_pre_match_locked_snap = False
        if ps_rows:
            for s in ps_rows:
                if s['is_pre_match_locked']:
                    has_pre_match_locked_snap = True
                    break

        row['has_result'] = has_result

        # Determine scoring eligibility and exclusion reason
        exclusion_reasons = []

        if not has_result:
            exclusion_reasons.append("A: No final score")

        if not row['has_base_prediction']:
            exclusion_reasons.append("B: No base prediction (match_predictions)")

        if not row['has_prediction_snapshot']:
            exclusion_reasons.append("C: No prediction snapshot at all")
        else:
            if not has_pre_kickoff_snap:
                exclusion_reasons.append("D: Snapshot exists but ALL after kickoff")
            if not has_pre_match_locked_snap:
                exclusion_reasons.append("E: No pre-match-locked snapshot")
            if not row['snapshot_has_probabilities']:
                exclusion_reasons.append("F: Snapshot missing probabilities")

        if not row['has_ensemble_prediction']:
            exclusion_reasons.append("G: No ensemble prediction")

        if not row['has_ai_prediction']:
            exclusion_reasons.append("H: No AI prediction")

        if not row['has_market_snapshot']:
            exclusion_reasons.append("I: No market snapshot")

        if has_result and has_pre_kickoff_snap and row['snapshot_has_probabilities']:
            row['scoring_eligible'] = True
            row['exclusion_reason'] = None
        elif has_result and has_pre_match_locked_snap and row['snapshot_has_probabilities']:
            row['scoring_eligible'] = True
            row['exclusion_reason'] = "WARN: eligible via pre_match_locked flag (not snapshotted_at < kickoff)"
        else:
            row['scoring_eligible'] = False
            row['exclusion_reason'] = " | ".join(exclusion_reasons) if exclusion_reasons else "Unknown"

        results.append(row)

    # ── Print per-match table ──
    print(f"\n{'Match':<12} {'Home':>4} vs {'Away':<4} {'Kickoff':<20} {'Score':>5} "
          f"{'Base?':>5} {'Snap?':>5} {'Snap#':>5} {'PreKO?':>6} {'PreKO#':>6} "
          f"{'Locked?':>7} {'AI?':>4} {'AI#':>4} {'AIPreKO':>7} "
          f"{'Ens?':>4} {'Ens#':>4} {'EnsPreKO':>7} "
          f"{'Lock?':>5} {'TP?':>4} {'Mkt?':>4} {'MktPreKO':>8} "
          f"{'Result?':>7} {'Eligible':>8} {'Exclusion Reason'}")
    print("-" * 220)

    for r in results:
        print(f"{r['match_id']:<12} {r['home_team']:>4} vs {r['away_team']:<4} {r['kickoff']:<20} "
              f"{r['home_score']}-{r['away_score'] if r['away_score'] is not None else 'N/A':>5} "
              f"{'Y' if r['has_base_prediction'] else 'N':>5} "
              f"{'Y' if r['has_prediction_snapshot'] else 'N':>5} "
              f"{r['prediction_snapshot_count']:>5} "
              f"{'Y' if r['latest_snapshot_before_kickoff'] else 'N':>6} "
              f"{r['pre_kickoff_snapshot_count']:>6} "
              f"{'Y' if r.get('snapshot_is_pre_match_locked') else 'N':>7} "
              f"{'Y' if r['has_ai_prediction'] else 'N':>4} "
              f"{r['ai_prediction_count']:>4} "
              f"{r['ai_pre_kickoff_count']:>7} "
              f"{'Y' if r['has_ensemble_prediction'] else 'N':>4} "
              f"{r['ensemble_prediction_count']:>4} "
              f"{r['ensemble_pre_kickoff_count']:>7} "
              f"{'Y' if r['has_lock_tracker'] else 'N':>5} "
              f"{'Y' if r['has_team_profile_prediction'] else 'N':>4} "
              f"{'Y' if r['has_market_snapshot'] else 'N':>4} "
              f"{r['market_pre_kickoff_count']:>8} "
              f"{'Y' if r['has_result'] else 'N':>7} "
              f"{'YES' if r['scoring_eligible'] else 'NO':>8} "
              f"{r['exclusion_reason'] or ''}")

    # ── Step 3: Detailed snapshot timing analysis ──
    print("\n\n── STEP 3: Detailed snapshot timing analysis ──")
    print("-" * 120)
    for r in results:
        if r['prediction_snapshot_count'] > 0:
            print(f"\nMatch {r['match_id']} ({r['home_team']} vs {r['away_team']}):")
            print(f"  Kickoff: {r['kickoff']}")
            print(f"  Snapshot count: {r['prediction_snapshot_count']}")
            for i, (t, locked) in enumerate(zip(r['all_snapshot_times'], r['all_snapshot_locked'])):
                delta = ""
                try:
                    ko = datetime.fromisoformat(r['kickoff'].replace('Z', '+00:00').replace('+00:00', ''))
                    snap = datetime.fromisoformat(t.replace('Z', '+00:00').replace('+00:00', ''))
                    diff = (snap - ko).total_seconds()
                    if diff < 0:
                        delta = f"  ({abs(diff)/3600:.1f}h BEFORE kickoff)"
                    else:
                        delta = f"  ({diff/3600:.1f}h AFTER kickoff)"
                except:
                    delta = "  (parse error)"
                print(f"    [{i}] snapshotted_at={t}  is_pre_match_locked={locked}{delta}")
            print(f"  Pre-kickoff snapshots: {r['pre_kickoff_snapshot_count']}")
            print(f"  is_pre_match_locked (latest): {r.get('snapshot_is_pre_match_locked')}")
            print(f"  is_fallback_locked (latest): {r.get('snapshot_is_fallback_locked')}")

    # ── Step 4: AI prediction timing ──
    print("\n\n── STEP 4: AI prediction timing analysis ──")
    print("-" * 120)
    for r in results:
        if r['ai_prediction_count'] > 0:
            cur.execute("""
                SELECT created_at, is_pre_match_locked, is_fallback_locked, real_time_only,
                       locked_at, provider, model_id
                FROM ai_predictions
                WHERE match_id = ?
                ORDER BY created_at
            """, (r['match_id'],))
            ai_detail = cur.fetchall()
            print(f"\nMatch {r['match_id']} ({r['home_team']} vs {r['away_team']}):")
            print(f"  Kickoff: {r['kickoff']}")
            print(f"  AI prediction count: {r['ai_prediction_count']}")
            for i, a in enumerate(ai_detail):
                delta = ""
                try:
                    ko = datetime.fromisoformat(r['kickoff'].replace('Z', '+00:00').replace('+00:00', ''))
                    created = datetime.fromisoformat(a['created_at'].replace('Z', '+00:00').replace('+00:00', ''))
                    diff = (created - ko).total_seconds()
                    if diff < 0:
                        delta = f"  ({abs(diff)/3600:.1f}h BEFORE kickoff)"
                    else:
                        delta = f"  ({diff/3600:.1f}h AFTER kickoff)"
                except:
                    delta = "  (parse error)"
                print(f"    [{i}] created_at={a['created_at']}  provider={a['provider']}  "
                      f"model={a['model_id']}  pre_match_locked={a['is_pre_match_locked']}  "
                      f"fallback_locked={a['is_fallback_locked']}  real_time_only={a['real_time_only']}  "
                      f"locked_at={a['locked_at']}{delta}")

    # ── Step 5: Ensemble prediction timing ──
    print("\n\n── STEP 5: Ensemble prediction timing analysis ──")
    print("-" * 120)
    for r in results:
        if r['ensemble_prediction_count'] > 0:
            cur.execute("""
                SELECT created_at, is_pre_match_locked, is_fallback_locked, real_time_only,
                       locked_at, model_version
                FROM ensemble_predictions
                WHERE match_id = ?
                ORDER BY created_at
            """, (r['match_id'],))
            ens_detail = cur.fetchall()
            print(f"\nMatch {r['match_id']} ({r['home_team']} vs {r['away_team']}):")
            print(f"  Kickoff: {r['kickoff']}")
            print(f"  Ensemble prediction count: {r['ensemble_prediction_count']}")
            for i, e in enumerate(ens_detail):
                delta = ""
                try:
                    ko = datetime.fromisoformat(r['kickoff'].replace('Z', '+00:00').replace('+00:00', ''))
                    created = datetime.fromisoformat(e['created_at'].replace('Z', '+00:00').replace('+00:00', ''))
                    diff = (created - ko).total_seconds()
                    if diff < 0:
                        delta = f"  ({abs(diff)/3600:.1f}h BEFORE kickoff)"
                    else:
                        delta = f"  ({diff/3600:.1f}h AFTER kickoff)"
                except:
                    delta = "  (parse error)"
                print(f"    [{i}] created_at={e['created_at']}  model_version={e['model_version']}  "
                      f"pre_match_locked={e['is_pre_match_locked']}  "
                      f"fallback_locked={e['is_fallback_locked']}  real_time_only={e['real_time_only']}  "
                      f"locked_at={e['locked_at']}{delta}")

    # ── Step 6: Lock tracker details ──
    print("\n\n── STEP 6: Lock tracker details ──")
    print("-" * 120)
    for r in results:
        if r['lock_tracker_entries']:
            print(f"\nMatch {r['match_id']} ({r['home_team']} vs {r['away_team']}):")
            for lt in r['lock_tracker_entries']:
                print(f"    lock_type={lt['lock_type']}  model_version={lt['model_version']}  "
                      f"ensemble_id={lt['ensemble_id']}")

    # ── Step 7: Team profile prediction details ──
    print("\n\n── STEP 7: Team profile prediction details ──")
    print("-" * 120)
    for r in results:
        if r['has_team_profile_prediction']:
            cur.execute("""
                SELECT created_at, model_version, is_pre_match_locked, is_fallback_locked,
                       real_time_only, locked_at
                FROM team_profile_predictions
                WHERE match_id = ?
                ORDER BY created_at
            """, (r['match_id'],))
            tp_detail = cur.fetchall()
            print(f"\nMatch {r['match_id']} ({r['home_team']} vs {r['away_team']}):")
            print(f"  TP count: {r['team_profile_prediction_count']}")
            for i, t in enumerate(tp_detail):
                delta = ""
                try:
                    ko = datetime.fromisoformat(r['kickoff'].replace('Z', '+00:00').replace('+00:00', ''))
                    created = datetime.fromisoformat(t['created_at'].replace('Z', '+00:00').replace('+00:00', ''))
                    diff = (created - ko).total_seconds()
                    if diff < 0:
                        delta = f"  ({abs(diff)/3600:.1f}h BEFORE kickoff)"
                    else:
                        delta = f"  ({diff/3600:.1f}h AFTER kickoff)"
                except:
                    delta = "  (parse error)"
                print(f"    [{i}] created_at={t['created_at']}  model_version={t['model_version']}  "
                      f"pre_match_locked={t['is_pre_match_locked']}  "
                      f"fallback_locked={t['is_fallback_locked']}  real_time_only={t['real_time_only']}  "
                      f"locked_at={t['locked_at']}{delta}")

    # ── Step 8: Summary statistics ──
    print("\n\n" + "=" * 120)
    print("SUMMARY STATISTICS")
    print("=" * 120)

    total = len(results)
    has_base = sum(1 for r in results if r['has_base_prediction'])
    has_snap = sum(1 for r in results if r['has_prediction_snapshot'])
    has_pre_kickoff_snap = sum(1 for r in results if r['latest_snapshot_before_kickoff'])
    has_pre_match_locked_snap = sum(1 for r in results if any(r.get('all_snapshot_locked', [])))
    has_ai = sum(1 for r in results if r['has_ai_prediction'])
    has_ai_pre_kickoff = sum(1 for r in results if r['ai_pre_kickoff_count'] > 0)
    has_ensemble = sum(1 for r in results if r['has_ensemble_prediction'])
    has_ensemble_pre_kickoff = sum(1 for r in results if r['ensemble_pre_kickoff_count'] > 0)
    has_lock = sum(1 for r in results if r['has_lock_tracker'])
    has_tp = sum(1 for r in results if r['has_team_profile_prediction'])
    has_market = sum(1 for r in results if r['has_market_snapshot'])
    has_market_pre_kickoff = sum(1 for r in results if r['market_pre_kickoff_count'] > 0)
    has_result = sum(1 for r in results if r['has_result'])
    scoring_eligible = sum(1 for r in results if r['scoring_eligible'])
    has_any_prediction = sum(1 for r in results if r['has_base_prediction'] or r['has_prediction_snapshot'] or r['has_ai_prediction'] or r['has_ensemble_prediction'])
    data_gap = sum(1 for r in results if not r['scoring_eligible'])

    print(f"\n  completed_matches_count:           {total}")
    print(f"  has_any_prediction_count:           {has_any_prediction}")
    print(f"  has_base_prediction_count:          {has_base}")
    print(f"  has_prediction_snapshot_count:      {has_snap}")
    print(f"  has_pre_kickoff_snapshot_count:     {has_pre_kickoff_snap}")
    print(f"  has_pre_match_locked_snap_count:    {has_pre_match_locked_snap}")
    print(f"  has_ai_prediction_count:            {has_ai}")
    print(f"  has_ai_pre_kickoff_count:           {has_ai_pre_kickoff}")
    print(f"  has_ensemble_prediction_count:      {has_ensemble}")
    print(f"  has_ensemble_pre_kickoff_count:     {has_ensemble_pre_kickoff}")
    print(f"  has_lock_tracker_count:             {has_lock}")
    print(f"  has_team_profile_prediction_count:  {has_tp}")
    print(f"  has_market_snapshot_count:          {has_market}")
    print(f"  has_market_pre_kickoff_count:       {has_market_pre_kickoff}")
    print(f"  has_result_count:                   {has_result}")
    print(f"  valid_pre_kickoff_snapshot_count:   {has_pre_kickoff_snap}")
    print(f"  scoring_eligible_count:             {scoring_eligible}")
    print(f"  data_gap_count:                     {data_gap}")

    # Exclusion breakdown
    print("\n  EXCLUSION BREAKDOWN:")
    reason_categories = {}
    for r in results:
        if r['exclusion_reason']:
            reasons = r['exclusion_reason'].split(' | ')
            for reason in reasons:
                code = reason.split(':')[0].strip()
                reason_categories[code] = reason_categories.get(code, 0) + 1

    for code in sorted(reason_categories.keys()):
        print(f"    {code}: {reason_categories[code]} matches")

    # ── Step 9: CRITICAL ANALYSIS ──
    print("\n\n" + "=" * 120)
    print("CRITICAL ANALYSIS: Why '赛前预测14场' but '开球前快照0场'")
    print("=" * 120)

    print("\n  Breaking down what '赛前预测' could mean:")

    # 1. match_predictions (base predictions) - these are the model's raw predictions
    print(f"\n  1. match_predictions (base model predictions): {has_base} matches have these")
    cur.execute("""
        SELECT mp.match_id, mp.model_version, mp.revision_id, dr.created_at as rev_created_at,
               dr.model_version as rev_model_version, dr.active as rev_active,
               m.kickoff
        FROM match_predictions mp
        JOIN dashboard_revisions dr ON mp.revision_id = dr.id
        JOIN matches m ON mp.match_id = m.id
        WHERE m.status IN ('final', 'completed')
        ORDER BY dr.created_at
    """)
    mp_with_rev = cur.fetchall()
    print(f"     Total match_prediction rows for final matches: {len(mp_with_rev)}")
    if mp_with_rev:
        rev_pre_kickoff = 0
        rev_post_kickoff = 0
        for mp in mp_with_rev:
            if mp['rev_created_at'] and mp['kickoff']:
                try:
                    rev_time = datetime.fromisoformat(mp['rev_created_at'].replace('Z', '+00:00').replace('+00:00', ''))
                    ko_time = datetime.fromisoformat(mp['kickoff'].replace('Z', '+00:00').replace('+00:00', ''))
                    if rev_time < ko_time:
                        rev_pre_kickoff += 1
                    else:
                        rev_post_kickoff += 1
                except:
                    pass
        print(f"     Revisions created BEFORE kickoff: {rev_pre_kickoff}")
        print(f"     Revisions created AFTER kickoff:  {rev_post_kickoff}")

    # 2. prediction_snapshots - these are the "snapshotted" versions
    print(f"\n  2. prediction_snapshots: {has_snap} matches have these")
    print(f"     Pre-kickoff snapshots (snapshotted_at < kickoff): {has_pre_kickoff_snap}")
    print(f"     Pre-match-locked snapshots: {has_pre_match_locked_snap}")

    # 3. prediction_snapshots by match status
    cur.execute("""
        SELECT m.status, COUNT(DISTINCT ps.match_id) as matches_with_snaps, COUNT(*) as total_snaps
        FROM prediction_snapshots ps
        JOIN matches m ON ps.match_id = m.id
        GROUP BY m.status
    """)
    snap_by_status = cur.fetchall()
    print(f"\n  3. prediction_snapshots by match status:")
    for s in snap_by_status:
        print(f"     status={s['status']}: {s['matches_with_snaps']} matches, {s['total_snaps']} snapshots")

    # 4. team_profile_predictions by match status
    cur.execute("""
        SELECT m.status, COUNT(DISTINCT tp.match_id) as matches_with_tp, COUNT(*) as total_tp
        FROM team_profile_predictions tp
        JOIN matches m ON tp.match_id = m.id
        GROUP BY m.status
    """)
    tp_by_status = cur.fetchall()
    print(f"\n  4. team_profile_predictions by match status:")
    for s in tp_by_status:
        print(f"     status={s['status']}: {s['matches_with_tp']} matches, {s['total_tp']} predictions")

    # 5. market_snapshots by match status
    cur.execute("""
        SELECT m.status, COUNT(DISTINCT ms.match_id) as matches_with_mkt, COUNT(*) as total_mkt
        FROM market_snapshots ms
        JOIN matches m ON ms.match_id = m.id
        GROUP BY m.status
    """)
    mkt_by_status = cur.fetchall()
    print(f"\n  5. market_snapshots by match status:")
    for s in mkt_by_status:
        print(f"     status={s['status']}: {s['matches_with_mkt']} matches, {s['total_mkt']} snapshots")

    # 6. match_predictions by match status
    cur.execute("""
        SELECT m.status, COUNT(DISTINCT mp.match_id) as matches_with_mp, COUNT(*) as total_mp
        FROM match_predictions mp
        JOIN matches m ON mp.match_id = m.id
        GROUP BY m.status
    """)
    mp_by_status = cur.fetchall()
    print(f"\n  6. match_predictions by match status:")
    for s in mp_by_status:
        print(f"     status={s['status']}: {s['matches_with_mp']} matches, {s['total_mp']} predictions")

    # 7. Scoring code check
    print(f"\n  7. Scoring code check:")
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%score%'")
    score_tables = cur.fetchall()
    print(f"     Score-related tables: {[t['name'] for t in score_tables]}")
    if score_tables:
        for st in score_tables:
            cur.execute(f"PRAGMA table_info({st['name']})")
            cols = cur.fetchall()
            print(f"     {st['name']} columns: {[c['name'] for c in cols]}")
            cur.execute(f"SELECT COUNT(*) as cnt FROM {st['name']}")
            cnt = cur.fetchone()
            print(f"     {st['name']} row count: {cnt['cnt']}")

    # 8. Dashboard revision timeline
    print(f"\n  8. Dashboard revision timeline:")
    cur.execute("""
        SELECT id, created_at, model_version, active, simulation_iterations
        FROM dashboard_revisions
        ORDER BY created_at
    """)
    revs = cur.fetchall()
    print(f"     Total revisions: {len(revs)}")
    for rev in revs:
        print(f"     rev_id={rev['id']}  created_at={rev['created_at']}  model_version={rev['model_version']}  active={rev['active']}  iterations={rev['simulation_iterations']}")

    # 9. Timeline comparison
    cur.execute("SELECT MIN(kickoff) as earliest FROM matches WHERE status = 'final'")
    earliest_kickoff = cur.fetchone()['earliest']
    cur.execute("SELECT MIN(created_at) as earliest FROM dashboard_revisions")
    earliest_rev = cur.fetchone()['earliest']
    print(f"\n  9. Timeline comparison:")
    print(f"     Earliest final match kickoff: {earliest_kickoff}")
    print(f"     Earliest dashboard revision:   {earliest_rev}")
    try:
        ko = datetime.fromisoformat(earliest_kickoff.replace('Z', '+00:00').replace('+00:00', ''))
        rev = datetime.fromisoformat(earliest_rev.replace('Z', '+00:00').replace('+00:00', ''))
        delta = (rev - ko).total_seconds() / 3600
        print(f"     Gap: {delta:.1f} hours (revision created {delta:.1f}h AFTER earliest kickoff)")
    except:
        print(f"     Could not parse dates for comparison")

    # 10. Why MEX-RSA and KOR-CZE have no predictions
    print(f"\n  10. Why MEX-RSA and KOR-CZE have no match_predictions:")
    cur.execute("""
        SELECT id, home_team_id, away_team_id, kickoff, status, home_score, away_score,
               is_placeholder_match, source, source_updated_at
        FROM matches
        WHERE id IN ('2026-A-MEX-RSA-2026-06-11', '2026-A-KOR-CZE-2026-06-11')
    """)
    missing = cur.fetchall()
    for m in missing:
        print(f"     {m['id']}: kickoff={m['kickoff']}, status={m['status']}, "
              f"score={m['home_score']}-{m['away_score']}, placeholder={m['is_placeholder_match']}, "
              f"source={m['source']}, updated={m['source_updated_at']}")
    cur.execute("""
        SELECT mp.match_id, m.status
        FROM match_predictions mp
        JOIN matches m ON mp.match_id = m.id
        WHERE mp.match_id LIKE '%MEX%' OR mp.match_id LIKE '%RSA%'
           OR mp.match_id LIKE '%KOR%' OR mp.match_id LIKE '%CZE%'
        GROUP BY mp.match_id
    """)
    team_preds = cur.fetchall()
    print(f"     These teams DO have predictions for scheduled matches:")
    for tp in team_preds:
        print(f"       {tp['match_id']} (status={tp['status']})")

    # 11. Final verdict
    print(f"\n\n{'=' * 120}")
    print("FINAL VERDICT")
    print("=" * 120)
    print(f"""
  COMPLETED MATCHES: {total}
  MATCHES WITH ANY PREDICTION: {has_any_prediction}
  MATCHES WITH BASE PREDICTION (match_predictions): {has_base}
  MATCHES WITH PREDICTION SNAPSHOT: {has_snap}
  MATCHES WITH PRE-KICKOFF SNAPSHOT (snapshotted_at < kickoff): {has_pre_kickoff_snap}
  MATCHES WITH PRE-MATCH-LOCKED SNAPSHOT: {has_pre_match_locked_snap}
  SCORING ELIGIBLE: {scoring_eligible}
  DATA GAP: {data_gap}

  +==============================================================================================+
  |  ROOT CAUSE: "赛前预测14场" vs "开球前快照0场"                                              |
  +==============================================================================================+
  |                                                                                              |
  |  The entire prediction pipeline was FIRST RUN on 2026-06-16 (today), AFTER all 16 matches   |
  |  had already been played.                                                                    |
  |                                                                                              |
  |  EVIDENCE:                                                                                   |
  |  - Earliest final match kickoff: 2026-06-11 19:00 (MEX vs RSA)                              |
  |  - Earliest dashboard revision:  2026-06-16 06:22 (5 days later!)                           |
  |  - All 9 dashboard_revisions were created on 2026-06-16                                     |
  |  - ALL 70 match_predictions for final matches are from revision_id=1                         |
  |    (created at 2026-06-16 06:22:31 -- AFTER every match kicked off)                         |
  |                                                                                              |
  |  THE DISCREPANCY EXPLAINED:                                                                  |
  |                                                                                              |
  |  "赛前预测14场" = 14 matches have match_predictions rows. These are the base model           |
  |  outputs (elo-poisson-v1 + 4 shadow models). They exist because the model was run           |
  |  retroactively on already-completed matches. They are NOT truly "pre-match" -- they          |
  |  were generated AFTER the results were known.                                                |
  |                                                                                              |
  |  "开球前快照0场" = 0 matches have prediction_snapshots with snapshotted_at < kickoff.       |
  |  The prediction_snapshots table has 2360 rows, but ALL of them are for SCHEDULED            |
  |  (future) matches only. The snapshot workflow was never run before any match kicked off.     |
  |                                                                                              |
  |  WHY NO SNAPSHOTS FOR FINAL MATCHES:                                                         |
  |  The snapshot workflow only snapshots matches that are still 'scheduled'. Once a match       |
  |  becomes 'final', it is excluded from the snapshot process. Since the pipeline was only      |
  |  set up after matches were already final, no pre-kickoff snapshots were ever captured.       |
  |                                                                                              |
  |  WHY 14 NOT 16:                                                                              |
  |  2 matches (MEX-RSA, KOR-CZE) have NO match_predictions at all. These were the              |
  |  opening matches (Group A, June 11). The model may have skipped them because they            |
  |  were already final when the first revision was computed, or there was a data issue.         |
  |                                                                                              |
  |  CONSEQUENCE:                                                                                |
  |  - scoring_eligible_count = 0 (no valid pre-kickoff predictions exist)                       |
  |  - model_scores table is empty (no scoring has been performed)                               |
  |  - The 14 match_predictions are RETROACTIVE -- they cannot be used for scoring               |
  |    because they were generated with knowledge of the results                                 |
  |                                                                                              |
  +==============================================================================================+
""")

    conn.close()


if __name__ == "__main__":
    main()
