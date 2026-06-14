type ProbabilityBarsProps = {
  homeWin: number;
  draw: number;
  awayWin: number;
  size?: "normal" | "compact";
};

export default function ProbabilityBars({ homeWin, draw, awayWin, size = "normal" }: ProbabilityBarsProps) {
  return (
    <div className={`prob-bars${size === "compact" ? " prob-bars--compact" : ""}`}>
      <div className="prob-bars__row">
        <span className="prob-bars__label">主胜</span>
        <div className="prob-bars__track">
          <div className="prob-bars__fill prob-bars__fill--home" style={{ width: `${homeWin * 100}%` }} />
        </div>
        <span className="prob-bars__value">{(homeWin * 100).toFixed(0)}%</span>
      </div>
      <div className="prob-bars__row">
        <span className="prob-bars__label">平局</span>
        <div className="prob-bars__track">
          <div className="prob-bars__fill prob-bars__fill--draw" style={{ width: `${draw * 100}%` }} />
        </div>
        <span className="prob-bars__value">{(draw * 100).toFixed(0)}%</span>
      </div>
      <div className="prob-bars__row">
        <span className="prob-bars__label">客胜</span>
        <div className="prob-bars__track">
          <div className="prob-bars__fill prob-bars__fill--away" style={{ width: `${awayWin * 100}%` }} />
        </div>
        <span className="prob-bars__value">{(awayWin * 100).toFixed(0)}%</span>
      </div>
    </div>
  );
}
