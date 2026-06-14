import type { ReactNode } from "react";

type SectionCardProps = {
  title: string;
  badge?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
};

export default function SectionCard({ title, badge, action, children, className }: SectionCardProps) {
  return (
    <section className={`section-card${className ? ` ${className}` : ""}`}>
      <div className="section-card__header">
        <h3 className="section-card__title">
          {title}
          {badge && <span className="section-card__badge">{badge}</span>}
        </h3>
        {action && <div className="section-card__action">{action}</div>}
      </div>
      <div className="section-card__body">{children}</div>
    </section>
  );
}
