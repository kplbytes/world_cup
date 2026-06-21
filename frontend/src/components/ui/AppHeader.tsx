import type { ReactNode } from "react";

type Mode = "home" | "compact";

type AppHeaderProps = {
  mode?: Mode;
  brand?: string;
  subtitle?: string;
  version?: string;
  modelVersion?: string;
  onSync?: () => void;
  syncing?: boolean;
  nav?: ReactNode;
};

export default function AppHeader({
  mode = "home",
  brand = "2026 世界杯预测工作台",
  subtitle,
  version,
  modelVersion,
  onSync,
  syncing,
  nav,
}: AppHeaderProps) {
  if (mode === "home") {
    return (
      <header className="app-header app-header--home">
        <div className="app-header__left">
          <div className="app-header__brand">{brand}</div>
          {subtitle && (
            <div className="app-header__subtitle">{subtitle}</div>
          )}
        </div>
        <div className="app-header__right">
          {version && (
            <span className="app-header__version">v{version}</span>
          )}
          {modelVersion && (
            <span className="app-header__model">{modelVersion}</span>
          )}
          {onSync && (
            <button
              className="app-header__sync-btn"
              onClick={onSync}
              disabled={syncing}
            >
              {syncing ? "同步中..." : "同步赛果"}
            </button>
          )}
        </div>
      </header>
    );
  }

  return (
    <header className="app-header app-header--compact">
      <div className="app-header__left">
        <div className="app-header__brand-sm">{brand}</div>
      </div>
      <div className="app-header__right">
        {version && (
          <span className="app-header__version">v{version}</span>
        )}
        {modelVersion && (
          <span className="app-header__model">{modelVersion}</span>
        )}
        {onSync && (
          <button
            className="app-header__sync-btn"
            onClick={onSync}
            disabled={syncing}
          >
            {syncing ? "同步中..." : "同步"}
          </button>
        )}
      </div>
      {nav && <nav className="app-header__nav">{nav}</nav>}
    </header>
  );
}
