import type { ReactNode } from "react";

import { label } from "@/lib/format";

export function Page({ title, subtitle, actions, children }: { title: string; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <div className="mx-auto max-w-[1380px] px-6 py-7">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold text-ink">{title}</h1>
          {subtitle ? <p className="mt-1 max-w-3xl text-sm text-muted">{subtitle}</p> : null}
        </div>
        {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
      </div>
      {children}
    </div>
  );
}

export function Card({ title, aside, children, className = "" }: { title?: ReactNode; aside?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-lg border border-line bg-card shadow-[0_1px_0_rgba(0,0,0,0.03)] ${className}`}>
      {title ? (
        <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-2.5">
          <h2 className="text-[15px] font-semibold text-ink">{title}</h2>
          {aside}
        </div>
      ) : null}
      <div className="p-4">{children}</div>
    </section>
  );
}

const ROUTE_STYLE: Record<string, string> = {
  qualified: "bg-forest-soft text-forest border-moss/40",
  nurture: "bg-amber-soft text-amber border-amber/30",
  disqualified: "bg-zinc-100 text-zinc-600 border-zinc-300",
};

export function RouteBadge({ route }: { route: string | null }) {
  if (!route) return <span className="text-xs text-muted">not scored</span>;
  return <span className={`inline-block rounded-full border px-2 py-0.5 text-xs font-medium capitalize ${ROUTE_STYLE[route] ?? ""}`}>{route}</span>;
}

const TONES: Record<string, string> = {
  green: "bg-forest-soft text-forest",
  amber: "bg-amber-soft text-amber",
  red: "bg-brick-soft text-brick",
  slate: "bg-slate-soft text-slate",
  gray: "bg-zinc-100 text-zinc-600",
};

export function Pill({ tone = "gray", children, title }: { tone?: keyof typeof TONES; children: ReactNode; title?: string }) {
  return (
    <span title={title} className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium ${TONES[tone]}`}>
      {children}
    </span>
  );
}

const STATUS_TONE: Record<string, keyof typeof TONES> = {
  sent: "green",
  scheduled: "slate",
  deferred: "amber",
  paused: "amber",
  cancelled: "gray",
  blocked: "red",
  pending: "amber",
  approved: "green",
  rejected: "red",
  superseded: "gray",
};

export function StatusPill({ status, title }: { status: string; title?: string }) {
  return (
    <Pill tone={STATUS_TONE[status] ?? "gray"} title={title}>
      {label(status)}
    </Pill>
  );
}

const REPLY_TONE: Record<string, keyof typeof TONES> = {
  interested: "green",
  meeting_request: "green",
  not_now: "amber",
  referral: "slate",
  objection: "red",
  unsubscribe: "red",
  out_of_office: "gray",
  bounce: "gray",
};

export function ReplyBadge({ value }: { value: string | null }) {
  if (!value) return <Pill>unclassified</Pill>;
  return <Pill tone={REPLY_TONE[value] ?? "gray"}>{label(value)}</Pill>;
}

export function ScoreBar({ score, max = 100 }: { score: number | null; max?: number }) {
  if (score === null || score === undefined) return <span className="text-xs text-muted">-</span>;
  const width = Math.max(0, Math.min(100, (score / max) * 100));
  const color = score >= 60 ? "bg-moss" : score >= 35 ? "bg-amber" : "bg-zinc-400";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-20 overflow-hidden rounded-full bg-zinc-200">
        <div className={`h-full ${color}`} style={{ width: `${width}%` }} />
      </div>
      <span className="w-7 text-right font-mono text-xs tabular-nums">{score}</span>
    </div>
  );
}

export function Button({
  children,
  onClick,
  tone = "primary",
  disabled,
  type = "button",
}: {
  children: ReactNode;
  onClick?: () => void;
  tone?: "primary" | "secondary" | "danger";
  disabled?: boolean;
  type?: "button" | "submit";
}) {
  const style =
    tone === "primary"
      ? "bg-forest text-white hover:bg-forest-deep"
      : tone === "danger"
        ? "bg-white text-brick border border-brick/40 hover:bg-brick-soft"
        : "bg-white text-ink border border-line hover:bg-zinc-50";
  return (
    <button type={type} onClick={onClick} disabled={disabled} className={`rounded-md px-3 py-1.5 text-sm font-medium transition disabled:opacity-50 ${style}`}>
      {children}
    </button>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-6 text-center text-sm text-muted">{children}</p>;
}

export function ErrorNote({ error }: { error: string | null }) {
  if (!error) return null;
  return <p className="my-3 rounded-md border border-brick/30 bg-brick-soft px-3 py-2 text-sm text-brick">{error}</p>;
}

export function Kpi({ label: name, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="rounded-lg border border-line bg-card px-4 py-3">
      <div className="text-xs uppercase tracking-wide text-muted">{name}</div>
      <div className="mt-1 font-serif text-2xl font-semibold tabular-nums text-ink">{value}</div>
      {hint ? <div className="mt-0.5 text-xs text-muted">{hint}</div> : null}
    </div>
  );
}
