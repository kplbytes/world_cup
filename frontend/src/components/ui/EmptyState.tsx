import type { ReactNode } from "react";

type EmptyStateProps = {
  icon?: string;
  title?: string;
  children?: ReactNode;
  tone?: "default" | "warn" | "error";
};

export default function EmptyState({ icon, title, children, tone = "default" }: EmptyStateProps) {
  return (
    <div className={`empty-state${tone !== "default" ? ` empty-state--${tone}` : ""}`}>
      {icon && <div className="empty-state__icon">{icon}</div>}
      {title && <div className="empty-state__title">{title}</div>}
      {children && <div className="empty-state__body">{children}</div>}
    </div>
  );
}
