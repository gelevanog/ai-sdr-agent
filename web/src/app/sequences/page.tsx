"use client";

import Link from "next/link";
import { useState } from "react";

import { Button, Card, Empty, ErrorNote, Page, StatusPill } from "@/components/ui";
import { post, useApi } from "@/lib/api";
import { localTime } from "@/lib/format";
import type { MessageRow } from "@/lib/types";

export default function SequencesPage() {
  const { data, error, reload } = useApi<MessageRow[]>("/api/messages");
  const [note, setNote] = useState<string | null>(null);
  const groups = new Map<number, MessageRow[]>();
  for (const m of data ?? []) groups.set(m.draft_id, [...(groups.get(m.draft_id) ?? []), m]);

  async function send(days: number) {
    try {
      setNote(JSON.stringify(await post("/api/send/run", { fast_forward_days: days })));
      reload();
    } catch (err) {
      setNote(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <Page
      title="Sequences"
      subtitle="Approved sequences: each step goes out on a weekday between 9:00 and 17:00 in the prospect's time zone. Caps defer, suppression and do-not-contact block, and a reply stops or pauses the rest."
      actions={
        <>
          <Button tone="secondary" onClick={() => send(0)}>Send due now</Button>
          <Button onClick={() => send(10)}>Demo: fast-forward 10 days</Button>
        </>
      }
    >
      <ErrorNote error={error} />
      {note ? <p className="mb-4 rounded-md bg-forest-soft px-3 py-2 font-mono text-xs text-forest">{note}</p> : null}
      {groups.size === 0 ? <Empty>No approved sequences yet. Approve a draft in the queue.</Empty> : null}
      <div className="grid gap-3 lg:grid-cols-2">
        {[...groups.entries()].map(([draftId, steps]) => (
          <Card
            key={draftId}
            title={
              <span>
                <Link className="text-forest hover:underline" href={`/accounts/${steps[0].account_id}`}>{steps[0].name}</Link>
                <span className="ml-2 text-xs font-normal text-muted">to {steps[0].to_name} &lt;{steps[0].to_email}&gt;</span>
              </span>
            }
            aside={<Link className="text-xs text-forest underline" href={`/review/${draftId}`}>draft {draftId}</Link>}
          >
            <ol className="relative ml-2 border-l border-line">
              {steps.map((m) => (
                <li key={m.id} className="mb-3 ml-4">
                  <span className={`absolute -left-[5px] mt-1.5 h-2.5 w-2.5 rounded-full ${m.status === "sent" ? "bg-moss" : m.status === "scheduled" ? "bg-slate" : "bg-zinc-300"}`} />
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <span className="font-medium">{m.step === 1 ? "Email 1" : `Follow-up ${m.step - 1}`}</span>
                    <StatusPill status={m.status} />
                    <span className="text-xs text-muted">{localTime(m.sent_at ?? m.scheduled_at, m.timezone)}</span>
                  </div>
                  <div className="truncate text-xs text-ink-soft">{m.subject}</div>
                  {m.status_reason ? <div className="text-[11px] text-muted">{m.status_reason}</div> : null}
                </li>
              ))}
            </ol>
            <div className="text-[11px] text-muted">approved by {steps[0].approved_by}</div>
          </Card>
        ))}
      </div>
    </Page>
  );
}
