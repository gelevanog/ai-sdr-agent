"use client";

import Link from "next/link";
import { useState } from "react";

import { Button, Card, ErrorNote, Kpi, Page, Pill } from "@/components/ui";
import { post, useApi } from "@/lib/api";
import { label, localTime } from "@/lib/format";
import type { AuditRow } from "@/lib/types";

interface Overview {
  funnel: Record<string, number>;
  model: { provider: string; model: string; free_only: boolean };
  crawl_mode: string;
  crm_mode: string;
  capture_only: boolean;
  limits: Record<string, number>;
  recent: AuditRow[];
  jobs: { status: string; n: number }[];
}

const STAGES: [string, string][] = [
  ["accounts", "Target accounts"],
  ["researched", "Researched with citations"],
  ["qualified", "Qualified"],
  ["drafted", "Drafted"],
  ["approved", "Approved by a human"],
  ["sent", "Emails sent (captured)"],
  ["replies", "Replies"],
  ["positive_replies", "Positive replies"],
  ["meetings", "Meetings held"],
];

export default function OverviewPage() {
  const { data, error, reload } = useApi<Overview>("/api/overview");
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  async function run(name: string, path: string, body: unknown = {}) {
    setBusy(name);
    setNote(null);
    try {
      const result = await post<Record<string, unknown>>(path, body);
      setNote(`${name}: ${JSON.stringify(result)}`);
      reload();
    } catch (err) {
      setNote(`${name} failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBusy(null);
    }
  }

  const f = data?.funnel ?? {};
  const top = Math.max(1, f.accounts ?? 1);
  return (
    <Page
      title="Pipeline overview"
      subtitle="Scout researches each account with citations, qualifies it against your ICP, drafts outreach where every personal detail is sourced, and sends nothing until a person approves it. Email goes to a capture server only."
      actions={
        <>
          <Button tone="secondary" disabled={busy !== null} onClick={() => run("Queued research", "/api/pipeline/run-all")}>
            Research new accounts
          </Button>
          <Button tone="secondary" disabled={busy !== null} onClick={() => run("Send due", "/api/send/run", { fast_forward_days: 0 })}>
            Send due emails
          </Button>
          <Button disabled={busy !== null} onClick={() => run("Fast-forward", "/api/send/run", { fast_forward_days: 10 })}>
            Demo: fast-forward 10 days
          </Button>
        </>
      }
    >
      <ErrorNote error={error} />
      {note ? <p className="mb-4 rounded-md bg-forest-soft px-3 py-2 font-mono text-xs text-forest">{note}</p> : null}
      <div className="mb-6 flex flex-wrap gap-2 text-xs">
        <Pill tone="slate">model: {data?.model.provider}/{data?.model.model}</Pill>
        {data?.model.free_only ? <Pill tone="green">free-only guard on</Pill> : null}
        <Pill tone={data?.capture_only ? "green" : "red"}>{data?.capture_only ? "capture-only sending (Mailpit)" : "real sending"}</Pill>
        <Pill tone="slate">crawl: {data?.crawl_mode}</Pill>
        <Pill tone="slate">CRM: {data?.crm_mode}</Pill>
        {data ? (
          <Pill tone="gray">
            caps: {data.limits.daily_send_cap}/day, {data.limits.per_domain_daily_cap}/domain/day
          </Pill>
        ) : null}
      </div>
      <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
        <Kpi label="Qualified" value={f.qualified ?? "-"} hint={`${f.nurture ?? 0} nurture · ${f.disqualified ?? 0} disqualified`} />
        <Kpi label="Waiting for review" value={<Link href="/review">{f.pending_review ?? "-"}</Link>} hint="drafts with checked claims" />
        <Kpi label="Approved" value={f.approved ?? "-"} hint={`${f.rejected ?? 0} rejected`} />
        <Kpi label="Sent" value={f.sent ?? "-"} hint={`${f.scheduled ?? 0} scheduled`} />
        <Kpi label="Replies" value={f.replies ?? "-"} hint={`${f.positive_replies ?? 0} positive · ${f.meetings ?? 0} meetings`} />
        <Kpi label="Blocked by the guard" value={f.injection_findings ?? "-"} hint={`${f.suppressed ?? 0} suppressed addresses`} />
      </div>
      <div className="grid gap-5 lg:grid-cols-[1.1fr_1fr]">
        <Card title="Funnel">
          <div className="space-y-2">
            {STAGES.map(([key, name]) => {
              const value = f[key] ?? 0;
              return (
                <div key={key} className="grid grid-cols-[190px_1fr_44px] items-center gap-3 text-sm">
                  <span className="text-ink-soft">{name}</span>
                  <div className="h-5 rounded bg-zinc-100">
                    <div className="h-5 rounded bg-forest/80" style={{ width: `${(value / top) * 100}%` }} />
                  </div>
                  <span className="text-right font-mono tabular-nums">{value}</span>
                </div>
              );
            })}
          </div>
        </Card>
        <Card title="Recent activity" aside={<Link className="text-xs text-forest underline" href="/audit">audit log</Link>}>
          <ul className="divide-y divide-line text-sm">
            {(data?.recent ?? []).map((row) => (
              <li key={row.id} className="flex items-baseline gap-3 py-1.5">
                <span className="w-32 shrink-0 font-mono text-[11px] text-muted">{localTime(row.ts)}</span>
                <span className="font-medium">{label(row.action)}</span>
                <span className="truncate text-muted">
                  {row.entity} {row.entity_id ?? ""} · {row.actor}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </Page>
  );
}
