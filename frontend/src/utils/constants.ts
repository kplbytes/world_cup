/** CSS class for AI model status badges. */
export const STATUS_CLASS: Record<string, string> = {
  ready: "good",
  disabled: "warn",
  disabled_no_key: "bad",
  error: "bad",
  unconfigured: "bad",
};

/** Human-readable label for AI model status. */
export const STATUS_LABELS: Record<string, string> = {
  ready: "就绪",
  disabled: "已禁用",
  disabled_no_key: "未配置密钥",
  error: "错误",
  unconfigured: "未配置",
};

/** Emoji icon for AI model status. */
export const STATUS_ICON: Record<string, string> = {
  ready: "🟢",
  disabled: "🟡",
  disabled_no_key: "🔴",
  error: "🔴",
  unconfigured: "⚪",
};
