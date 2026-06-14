import type { ReactNode } from "react";

type PageShellProps = {
  children: ReactNode;
  wide?: boolean;
  className?: string;
};

export default function PageShell({ children, wide = false, className }: PageShellProps) {
  return (
    <div className={`page-container${wide ? " page-container--wide" : ""}${className ? ` ${className}` : ""}`}>
      {children}
    </div>
  );
}
