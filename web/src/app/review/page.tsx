"use client";

import Link from "next/link";

import { Card, Empty, ErrorNote, Page, Pill, ScoreBar } from "@/components/ui";
import { useApi } from "@/lib/api";
import { localTime } from "@/lib/format";
import type { QueueItem } from "@/lib/types";

export default function QueuePage() {
  const { data, error } = useApi<QueueItem[]>("/api/drafts?status=pending");
  const byAccount = new Map<number, QueueItem[]>();
  for (const item of data ?? []) byAccount.set(item.account_id, [...(byAccount.get(item.account_id) ?? []), item]);
  return (
    <Page
      title="Approval queue"
      subtitle="Nothing is sent until a person approves it. Each draft has passed the claim checker: every personal detail is tied to a quote from the prospect's website, and anything it could not verify was rewritten or removed."
    >
      <ErrorNote error={error} />
      {data && data.length === 0 ? <Empty>The queue is empty.</Empty> : null}
      <div className="grid gap-3 lg:grid-cols-2">
        {[...byAccount.entries()].map(([accountId, items]) => (
          <Card
            key={accountId}
            title={
              <span>
                {items[0].name} <span className="ml-1 font-mono text-[11px] font-normal text-muted">{items[0].domain}</span>
              </span>
            }
            aside={<ScoreBar score={items[0].score} />}
          >
            <div className="mb-2 text-xs text-muted">
              To {items[0].contact.name}, {items[0].contact.title} · {items[0].timezone} · drafted {localTime(items[0].created_at)}
            </div>
            <ul className="divide-y divide-line">
              {items.map((item) => {
                const blocks = item.report?.issues.filter((i) => i.severity === "block").length ?? 0;
                const claims = item.report?.personalization ?? 0;
                return (
                  <li key={item.id} className="flex items-center gap-3 py-2">
                    <span className="rounded bg-slate-soft px-1.5 py-0.5 font-mono text-xs text-slate">{item.variant}</span>
                    <Link href={`/review/${item.id}`} className="flex-1 truncate font-medium text-forest hover:underline">
                      {item.subject}
                    </Link>
                    <Pill tone={item.report?.passed ? "green" : "red"}>{item.report?.passed ? "checker passed" : `${blocks} issue(s)`}</Pill>
                    <Pill tone="slate">{claims} cited fact(s)</Pill>
                    {item.attempts > 1 ? <Pill tone="amber">rewritten {item.attempts - 1}×</Pill> : null}
                  </li>
                );
              })}
            </ul>
          </Card>
        ))}
      </div>
    </Page>
  );
}
