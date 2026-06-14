"""Adjustment Engine to generate AutoAdjustments from MatchIntelligence."""

from sqlalchemy.orm import Session
from sqlalchemy import delete
from app.models import Match, MatchIntelligence, AutoAdjustment
from app.prediction.poisson import MatchPredictionResult

class AdjustmentEngine:
    def __init__(self, session: Session, model_version: str):
        self.session = session
        self.model_version = model_version

    def clear_auto_adjustments(self):
        """Clear all auto adjustments before recomputing."""
        self.session.execute(delete(AutoAdjustment))
        self.session.flush()

    def evaluate_match(
        self,
        match: Match,
        base_prediction: MatchPredictionResult,
        intelligences: list[MatchIntelligence]
    ) -> list[AutoAdjustment]:
        """Evaluate intelligence and generate AutoAdjustments.

        Currently handles 'odds' intelligence (Sporttery divergence).
        """
        adjustments = []

        odds_intel = [i for i in intelligences if i.intelligence_type == "odds"]
        if odds_intel:
            # They are assumed to be sorted by fetched_at desc if we passed them correctly
            # Or we just take the one with highest ID (latest)
            latest_odds = max(odds_intel, key=lambda i: i.id)
            payload = latest_odds.normalized_payload

            home_prob = payload.get("home", payload.get("home_probability", payload.get("home_win", 0)))
            draw_prob = payload.get("draw", payload.get("draw_probability", 0))
            away_prob = payload.get("away", payload.get("away_probability", payload.get("away_win", 0)))

            home_diff = base_prediction.home_win - home_prob
            draw_diff = base_prediction.draw - draw_prob
            away_diff = base_prediction.away_win - away_prob

            diffs = {
                "主胜": (home_diff, base_prediction.home_win, home_prob),
                "平局": (draw_diff, base_prediction.draw, draw_prob),
                "客胜": (away_diff, base_prediction.away_win, away_prob)
            }
            max_outcome, (max_diff, base_prob, market_prob) = max(diffs.items(), key=lambda x: abs(x[1][0]))

            abs_diff = abs(max_diff)
            direction = "看衰" if max_diff > 0 else "看好"

            if abs_diff < 0.08:
                level = "低"
                confidence_penalty = 0.0
            elif abs_diff < 0.18:
                level = "中"
                confidence_penalty = -0.1
            else:
                level = "高"
                confidence_penalty = -0.25

            if level in ("中", "高"):
                reason_str = f"【{max_outcome}】模型预测 {base_prob:.1%} vs 市场隐含 {market_prob:.1%}，差异 {abs_diff:.1%} (市场{direction}) - {level}风险"
                adj = AutoAdjustment(
                    match_id=match.id,
                    source_intelligence_ids=[latest_odds.id],
                    affected_team_id=match.home_team_id,
                    adjustment_type="market_divergence",
                    attack_delta=0.0,
                    defense_delta=0.0,
                    draw_delta=0.0,
                    confidence=confidence_penalty,
                    reason=reason_str,
                    model_version=self.model_version
                )
                self.session.add(adj)
                adjustments.append(adj)

        # 2. Lineups completeness check
        lineups_intel = [i for i in intelligences if i.intelligence_type == "lineups"]

        # We only generate warning if we are within T-60
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        kickoff = match.kickoff
        if kickoff and kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)

        if kickoff and now >= kickoff - timedelta(minutes=60) and now < kickoff:
            has_official = any(i.normalized_payload.get("is_official") is True for i in lineups_intel)
            if not has_official:
                reason_str = "离比赛不到 60 分钟，仍未获取到官方首发信息"
                if not lineups_intel:
                    reason_str = "首发数据完全缺失"

                adj = AutoAdjustment(
                    match_id=match.id,
                    source_intelligence_ids=[i.id for i in lineups_intel],
                    affected_team_id=match.home_team_id,
                    adjustment_type="data_completeness",
                    attack_delta=0.0,
                    defense_delta=0.0,
                    draw_delta=0.0,
                    confidence=-0.05, # Small penalty
                    reason=reason_str,
                    model_version=self.model_version
                )
                self.session.add(adj)
                adjustments.append(adj)

        # 3. Roster injuries / suspensions
        roster_intel = [i for i in intelligences if i.intelligence_type in ("injuries", "suspensions")]
        if roster_intel:
            from collections import defaultdict
            from app.intelligence.player_mock import get_player_importance
            from app.config import settings

            team_intel = defaultdict(list)
            for intel in roster_intel:
                team_id = intel.normalized_payload.get("affected_team_id")
                if team_id:
                    team_intel[team_id].append(intel)

            for team_id, intels in team_intel.items():
                player_names = []
                total_attack_delta = 0.0
                total_defense_delta = 0.0

                for i in intels:
                    name = i.normalized_payload.get("player_name", "Unknown")
                    reason = i.normalized_payload.get("reason", "Out")

                    if settings.enable_numerical_adjustments:
                        p_mock = get_player_importance(name, team_id)
                        if p_mock:
                            impact = p_mock.importance_score * 0.08
                            if p_mock.position == "FWD":
                                a_w, d_w = 1.0, 0.0
                            elif p_mock.position == "MID":
                                a_w, d_w = 0.5, 0.5
                            elif p_mock.position == "DEF":
                                a_w, d_w = 0.0, 1.0
                            elif p_mock.position == "GK":
                                a_w, d_w = 0.0, 1.25
                            else:
                                a_w, d_w = 0.0, 0.0

                            a_delta = - (impact * a_w)
                            d_delta = - (impact * d_w)

                            # Cap single player impact at 0.08 (absolute)
                            a_delta = max(a_delta, -0.08)
                            d_delta = max(d_delta, -0.08)

                            total_attack_delta += a_delta
                            total_defense_delta += d_delta

                            player_names.append(f"{name}(重要性{p_mock.importance_score})")
                        else:
                            player_names.append(f"{name}({reason})")
                    else:
                        player_names.append(f"{name}({reason})")

                # Team cap -0.15
                total_attack_delta = max(total_attack_delta, -0.15)
                total_defense_delta = max(total_defense_delta, -0.15)

                summary = ", ".join(player_names)
                team_desc = "主队" if team_id == match.home_team_id else ("客队" if team_id == match.away_team_id else "球队")

                reason_str = f"{team_desc}存在伤停名单: {summary}"

                # Emit standard roster warning (no numeric deltas)
                adj_warning = AutoAdjustment(
                    match_id=match.id,
                    source_intelligence_ids=[i.id for i in intels],
                    affected_team_id=team_id,
                    adjustment_type="roster_warning",
                    attack_delta=0.0,
                    defense_delta=0.0,
                    draw_delta=0.0,
                    confidence=0.8,
                    reason=reason_str,
                    model_version=self.model_version
                )
                self.session.add(adj_warning)
                adjustments.append(adj_warning)

                # Emit numerical adjustment if enabled
                if settings.enable_numerical_adjustments and (total_attack_delta < 0 or total_defense_delta < 0):
                    num_reason_str = f"【实验性数值修正开启】{team_desc}缺阵: {summary}，attack_delta={total_attack_delta:.2f}, defense_delta={total_defense_delta:.2f}"
                    adj_numeric = AutoAdjustment(
                        match_id=match.id,
                        source_intelligence_ids=[i.id for i in intels],
                        affected_team_id=team_id,
                        adjustment_type="numerical_roster_adjustment",
                        attack_delta=total_attack_delta,
                        defense_delta=total_defense_delta,
                        draw_delta=0.0,
                        confidence=0.8,
                        reason=num_reason_str,
                        model_version=self.model_version
                    )
                    self.session.add(adj_numeric)
                    adjustments.append(adj_numeric)

        self.session.flush()
        return adjustments
