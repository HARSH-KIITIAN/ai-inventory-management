import type { ReactNode } from "react";

export function Badge({ kind, children }: { kind: string; children: ReactNode }) {
  return <span className={`badge badge-${kind}`}>{children}</span>;
}

export function Section({ title, right, children }: { title: string; right?: ReactNode; children: ReactNode }) {
  return (
    <section className="section">
      <div className="section-head">
        <h3>{title}</h3>
        {right}
      </div>
      {children}
    </section>
  );
}

// A labelled progress bar (e.g. budget used, storage used).
export function Bar({ label, used, total, unit }: { label: string; used: number; total: number; unit: string }) {
  const pct = total > 0 ? Math.min(100, Math.round((used / total) * 100)) : 0;
  const over = used > total;
  return (
    <div className="bar">
      <div className="bar-label">
        <span>{label}</span>
        <span>
          {used.toLocaleString()} / {total.toLocaleString()} {unit}
        </span>
      </div>
      <div className="bar-track">
        <div className={`bar-fill ${over ? "bar-over" : ""}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
