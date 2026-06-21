import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import MatchSummaryCard from "../components/MatchSummaryCard";
import MatchCard from "../components/MatchCard";
import ProbabilityBar from "../components/ProbabilityBar";
import ProbabilityBars from "../components/ui/ProbabilityBars";
import EmptyState from "../components/ui/EmptyState";
import RiskBadge from "../components/ui/RiskBadge";
import MetricCard from "../components/ui/MetricCard";
import SectionCard from "../components/ui/SectionCard";
import StatusStrip from "../components/ui/StatusStrip";
import GroupNav from "../components/GroupNav";
import Header from "../components/Header";
import type { Match, Dashboard } from "../types";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function makeMatch(overrides: Partial<Match> = {}): Match {
  return {
    id: "test-match",
    group_code: "A",
    kickoff: "2026-06-14T10:00:00Z",
    venue: "Test Stadium",
    status: "scheduled",
    home_team: { id: "BRA", name: "巴西", short_name: "巴西", flag: "🇧🇷" },
    away_team: { id: "ARG", name: "阿根廷", short_name: "阿根廷", flag: "🇦🇷" },
    home_score: null,
    away_score: null,
    manual_adjustments: [],
    intelligence: [],
    auto_adjustments: [],
    risk_flags: [],
    snapshot_status: { locked: false, locked_at: null, is_fallback: false, participates_in_model_score: false, real_time_only: false },
    prediction: {
      home_xg: 1.4,
      away_xg: 1.0,
      home_win: 0.5,
      draw: 0.3,
      away_win: 0.2,
      scorelines: [
        { home_goals: 1, away_goals: 0, probability: 0.12 },
        { home_goals: 1, away_goals: 1, probability: 0.10 },
      ],
      confidence: 0.8,
      confidence_label: "高",
      data_confidence: 0.85,
      data_confidence_label: "高",
      model_confidence: 0.22,
      model_confidence_label: "中",
      explanation: "",
      model_inputs: {},
      model_version: "elo-poisson-v1",
    },
    market: null,
    source: "test",
    source_updated_at: "2026-06-14T00:00:00Z",
    ...overrides,
  } as Match;
}

// ── MatchSummaryCard ─────────────────────────────────────────────────

describe("MatchSummaryCard", () => {
  it("renders team names and kickoff time", () => {
    const match = makeMatch();
    render(<MatchSummaryCard match={match} />);
    expect(screen.getByText("巴西")).toBeVisible();
    expect(screen.getByText("阿根廷")).toBeVisible();
  });

  it("shows score for finished matches", () => {
    const match = makeMatch({
      status: "final",
      home_score: 2,
      away_score: 1,
    });
    render(<MatchSummaryCard match={match} />);
    expect(screen.getByText("2 : 1")).toBeVisible();
  });

  it("shows vs for upcoming matches", () => {
    const match = makeMatch();
    render(<MatchSummaryCard match={match} />);
    expect(screen.getByText("vs")).toBeVisible();
  });

  it("shows recommendation label", () => {
    const match = makeMatch();
    render(<MatchSummaryCard match={match} />);
    expect(screen.getByText(/巴西胜/)).toBeVisible();
  });

  it("shows detail button", () => {
    const match = makeMatch();
    render(<MatchSummaryCard match={match} />);
    expect(screen.getByText("查看分析")).toBeVisible();
  });

  it("calls onOpenDetails when detail button is clicked", async () => {
    const match = makeMatch();
    const onOpen = vi.fn();
    render(<MatchSummaryCard match={match} onOpenDetails={onOpen} />);
    await userEvent.click(screen.getByText("查看分析"));
    expect(onOpen).toHaveBeenCalledWith(match);
  });
});

// ── ProbabilityBar ───────────────────────────────────────────────────

describe("ProbabilityBar", () => {
  it("renders label and percentage", () => {
    const { container } = render(<ProbabilityBar label="主胜" value={0.65} />);
    expect(container.textContent).toContain("主胜");
    expect(container.textContent).toContain("65.0%");
  });

  it("renders N/A for null value", () => {
    const { container } = render(<ProbabilityBar label="主胜" value={null as unknown as number} />);
    expect(container.textContent).toContain("N/A");
  });
});

// ── ProbabilityBars ──────────────────────────────────────────────────

describe("ProbabilityBars", () => {
  it("renders three probability rows", () => {
    render(<ProbabilityBars homeWin={0.5} draw={0.3} awayWin={0.2} />);
    expect(screen.getByText("主胜")).toBeVisible();
    expect(screen.getByText("平局")).toBeVisible();
    expect(screen.getByText("客胜")).toBeVisible();
  });

  it("displays correct percentages", () => {
    render(<ProbabilityBars homeWin={0.55} draw={0.25} awayWin={0.2} />);
    expect(screen.getByText("55%")).toBeVisible();
    expect(screen.getByText("25%")).toBeVisible();
    expect(screen.getByText("20%")).toBeVisible();
  });
});

// ── EmptyState ───────────────────────────────────────────────────────

describe("EmptyState", () => {
  it("renders title when provided", () => {
    render(<EmptyState title="暂无数据" />);
    expect(screen.getByText("暂无数据")).toBeVisible();
  });

  it("renders children when provided", () => {
    render(<EmptyState>自定义内容</EmptyState>);
    expect(screen.getByText("自定义内容")).toBeVisible();
  });
});

// ── RiskBadge ────────────────────────────────────────────────────────

describe("RiskBadge", () => {
  it("renders risk level text", () => {
    const { container } = render(<RiskBadge level="high" />);
    expect(container.textContent).toContain("高风险");
  });

  it("renders medium risk", () => {
    const { container } = render(<RiskBadge level="medium" />);
    expect(container.textContent).toContain("中风险");
  });

  it("renders low risk", () => {
    const { container } = render(<RiskBadge level="low" />);
    expect(container.textContent).toContain("低风险");
  });
});

// ── MetricCard ───────────────────────────────────────────────────────

describe("MetricCard", () => {
  it("renders label and value", () => {
    render(<MetricCard label="样本数" value={42} tone="ok" />);
    expect(screen.getByText("样本数")).toBeVisible();
    expect(screen.getByText("42")).toBeVisible();
  });

  it("renders note when provided", () => {
    render(<MetricCard label="样本数" value={42} tone="ok" note="充分" />);
    expect(screen.getByText("充分")).toBeVisible();
  });
});

// ── SectionCard ──────────────────────────────────────────────────────

describe("SectionCard", () => {
  it("renders title", () => {
    render(<SectionCard title="模型评分">Content</SectionCard>);
    expect(screen.getByText("模型评分")).toBeVisible();
  });

  it("renders badge when provided", () => {
    render(<SectionCard title="模型评分" badge="v1.1">Content</SectionCard>);
    expect(screen.getByText("v1.1")).toBeVisible();
  });

  it("renders children content", () => {
    render(<SectionCard title="Section">子内容</SectionCard>);
    expect(screen.getByText("子内容")).toBeVisible();
  });

  it("renders action when provided", () => {
    render(<SectionCard title="Section" action={<button>操作</button>}>Content</SectionCard>);
    expect(screen.getByText("操作")).toBeVisible();
  });
});

// ── StatusStrip ──────────────────────────────────────────────────────

describe("StatusStrip", () => {
  it("renders all items", () => {
    const items = [
      { label: "已评分", value: "5", tone: "ok" as const },
      { label: "待评分", value: "2", tone: "warn" as const },
    ];
    render(<StatusStrip items={items} />);
    expect(screen.getByText("已评分")).toBeVisible();
    expect(screen.getByText("5")).toBeVisible();
    expect(screen.getByText("待评分")).toBeVisible();
    expect(screen.getByText("2")).toBeVisible();
  });

  it("renders items without tone as neutral", () => {
    const items = [{ label: "状态", value: "正常" }];
    render(<StatusStrip items={items} />);
    expect(screen.getByText("正常")).toBeVisible();
  });
});

// ── GroupNav ─────────────────────────────────────────────────────────

describe("GroupNav", () => {
  it("renders all 12 group buttons (A-L)", () => {
    const onSelect = vi.fn();
    render(<GroupNav selected="A" onSelect={onSelect} />);
    const buttons = screen.getAllByRole("button");
    expect(buttons).toHaveLength(12);
    expect(buttons[0].textContent).toBe("A");
    expect(buttons[11].textContent).toBe("L");
  });

  it("marks the selected group as active", () => {
    const onSelect = vi.fn();
    render(<GroupNav selected="C" onSelect={onSelect} />);
    const selectedBtn = screen.getByRole("button", { pressed: true });
    expect(selectedBtn.textContent).toBe("C");
  });

  it("calls onSelect when a group is clicked", async () => {
    const onSelect = vi.fn();
    render(<GroupNav selected="A" onSelect={onSelect} />);
    await userEvent.click(screen.getByText("D"));
    expect(onSelect).toHaveBeenCalledWith("D");
  });
});

// ── Header ───────────────────────────────────────────────────────────

describe("Header", () => {
  const dashboard: Dashboard = {
    revision: { id: 1, created_at: "2026-06-14T04:00:00Z", model_version: "elo-poisson-v1", simulation_iterations: 50000, simulation_seed: 42 },
    groups: [],
    data_sources: [],
  };

  it("renders title and model version", () => {
    render(<Header dashboard={dashboard} refreshing={false} onRefresh={() => {}} />);
    expect(screen.getByText("2026 世界杯预测工作台")).toBeVisible();
    expect(screen.getByText("elo-poisson-v1")).toBeVisible();
  });

  it("shows sync button when not refreshing", () => {
    render(<Header dashboard={dashboard} refreshing={false} onRefresh={() => {}} />);
    expect(screen.getByText("同步赛果")).toBeVisible();
  });

  it("shows syncing text when refreshing", () => {
    render(<Header dashboard={dashboard} refreshing={true} onRefresh={() => {}} />);
    expect(screen.getByText("正在同步")).toBeVisible();
  });

  it("calls onRefresh when sync button is clicked", async () => {
    const onRefresh = vi.fn();
    render(<Header dashboard={dashboard} refreshing={false} onRefresh={onRefresh} />);
    await userEvent.click(screen.getByText("同步赛果"));
    expect(onRefresh).toHaveBeenCalled();
  });

  it("disables sync button when refreshing", () => {
    render(<Header dashboard={dashboard} refreshing={true} onRefresh={() => {}} />);
    expect(screen.getByText("正在同步")).toBeDisabled();
  });
});

// ── MatchCard ────────────────────────────────────────────────────────

describe("MatchCard", () => {
  it("renders team names and kickoff time", () => {
    const match = makeMatch();
    render(<MatchCard match={match} />);
    expect(screen.getByText(/巴西/)).toBeVisible();
    expect(screen.getByText(/阿根廷/)).toBeVisible();
  });

  it("shows VS for upcoming matches", () => {
    const match = makeMatch();
    render(<MatchCard match={match} />);
    expect(screen.getByText("VS")).toBeVisible();
  });

  it("shows score for finished matches", () => {
    const match = makeMatch({ status: "final", home_score: 3, away_score: 1 });
    render(<MatchCard match={match} />);
    expect(screen.getByText("3 : 1")).toBeVisible();
  });

  it("shows 终场 for finished matches", () => {
    const match = makeMatch({ status: "final", home_score: 1, away_score: 0 });
    render(<MatchCard match={match} />);
    expect(screen.getByText("终场")).toBeVisible();
  });

  it("shows confidence label for upcoming matches", () => {
    const match = makeMatch();
    render(<MatchCard match={match} />);
    expect(screen.getByText("高")).toBeVisible();
  });

  it("shows detail button", () => {
    const match = makeMatch();
    render(<MatchCard match={match} />);
    expect(screen.getByText("查看详情")).toBeVisible();
  });

  it("shows prediction direction labels", () => {
    const match = makeMatch();
    render(<MatchCard match={match} />);
    expect(screen.getByText("Baseline")).toBeVisible();
    expect(screen.getByText("AI")).toBeVisible();
    expect(screen.getByText("Ensemble")).toBeVisible();
  });

  it("shows 待运行 when no AI prediction", () => {
    const match = makeMatch();
    render(<MatchCard match={match} />);
    expect(screen.getByText("待运行")).toBeVisible();
  });

  it("shows 待生成 when no Ensemble prediction", () => {
    const match = makeMatch();
    render(<MatchCard match={match} />);
    expect(screen.getByText("待生成")).toBeVisible();
  });

  it("shows locked status when snapshot is locked", () => {
    const match = makeMatch({
      snapshot_status: { locked: true, locked_at: "2026-06-14T00:00:00Z", is_fallback: false, participates_in_model_score: true, real_time_only: false },
    });
    render(<MatchCard match={match} />);
    expect(screen.getByText("已锁定")).toBeVisible();
  });

  it("shows real-time status when snapshot is real_time_only", () => {
    const match = makeMatch({
      snapshot_status: { locked: false, locked_at: null, is_fallback: false, participates_in_model_score: false, real_time_only: true },
    });
    render(<MatchCard match={match} />);
    expect(screen.getByText("实时")).toBeVisible();
  });

  it("calls onOpenDetails when detail button is clicked", async () => {
    const match = makeMatch();
    const onOpen = vi.fn();
    render(<MatchCard match={match} onOpenDetails={onOpen} />);
    await userEvent.click(screen.getByText("查看详情"));
    expect(onOpen).toHaveBeenCalledWith(match);
  });
});
