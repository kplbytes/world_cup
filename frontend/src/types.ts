export type Probability = {
  first: number; second: number; third: number; fourth: number;
  qualify: number; standard_error: number;
};

export type Standing = {
  position: number; played: number; won: number; drawn: number; lost: number;
  goals_for: number; goals_against: number; goal_difference: number; points: number;
  tiebreak_uncertain: boolean;
};

export type Team = {
  id: string; name: string; short_name: string; code: string; flag: string | null;
  elo: number; fifa_rank: number | null; fifa_points: number | null; recent_form: string;
  standing: Standing; qualification: Probability;
};

export type TeamRef = Pick<Team, "id" | "name" | "short_name" | "flag">;

export type Scoreline = { home_goals: number; away_goals: number; probability: number };

export type Prediction = {
  home_xg: number; away_xg: number; home_win: number; draw: number; away_win: number;
  scorelines: Scoreline[]; confidence: number; confidence_label: string;
  data_confidence: number | null; data_confidence_label: string | null;
  model_confidence: number | null; model_confidence_label: string | null;
  explanation: string; model_inputs: { home_elo: number; away_elo: number }; model_version: string;
};

export type Divergence = {
  home_diff: number; draw_diff: number; away_diff: number;
  max_divergence: number; level: string;
};

export type MarketData = {
  home_probability: number; draw_probability: number; away_probability: number;
  raw_overround: number; divergence: Divergence | null;
};

export type Match = {
  id: string; group_code: string; kickoff: string; venue: string | null; status: string;
  home_team: TeamRef; away_team: TeamRef; home_score: number | null; away_score: number | null;
  prediction: Prediction | null; market: MarketData | null; source: string; source_updated_at: string | null;
};

export type Group = { code: string; name: string; teams: Team[]; matches: Match[] };

export type DataSource = {
  provider: string; source_url: string; fetched_at: string; status: string;
  coverage: Record<string, number>; error: string | null;
};

export type Dashboard = {
  revision: { id: number; created_at: string; model_version: string; simulation_iterations: number; simulation_seed: number };
  groups: Group[];
  data_sources: DataSource[];
};

export type DecisionPrediction = {
  home_win: number; draw: number; away_win: number;
  confidence_label: string; model_confidence_label: string | null;
  home_xg: number; away_xg: number;
};

export type DecisionMarket = {
  home_probability: number; draw_probability: number; away_probability: number;
  divergence: { max_divergence: number; level: string } | null;
};

export type DecisionMatch = {
  id: string; group_code: string; kickoff: string;
  home_team: TeamRef; away_team: TeamRef;
  status: string; home_score: number | null; away_score: number | null;
  prediction?: DecisionPrediction; market?: DecisionMarket;
};

export type ReviewMatch = DecisionMatch & {
  snapshot?: { home_win: number; draw: number; away_win: number; outcome_correct: boolean };
};

export type DecisionData = {
  today_matches: DecisionMatch[];
  most_confident: DecisionMatch[];
  most_uncertain: DecisionMatch[];
  biggest_divergence: DecisionMatch[];
  upset_risk: DecisionMatch[];
  recent_review: ReviewMatch[];
};

