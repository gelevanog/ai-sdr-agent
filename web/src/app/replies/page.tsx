"use client";

import Link from "next/link";
import { useState } from "react";

import { Button, Card, Empty, ErrorNote, Page, Pill, ReplyBadge } from "@/components/ui";
import { post, useApi } from "@/lib/api";
import { label, localTime } from "@/lib/format";
import type { ReplyRow } from "@/lib/types";

export default function RepliesPage() {
  const { data, error, reload } = useApi<ReplyRow[]>("/api/replies");
  const [note, setNote] = useState<string | null>(null);
  const [from, setFrom] = useState("");
  const [body, setBody] = useState("");

  async function simulate() {
    try {
      setNote(JSON.stringify(await post("/api/replies/simulate")));
      reload();
    } catch (err) {
      setNote(err instanceof Error ? err.message : String(err));
    }
  }

  async function manual() {
    try {
      setNote(JSON.stringify(await post("/api/replies", { from_email: from, body })));
      setBody("");
      reload();
    } catch (err) {
      setNote(err instanceof Error ? err.message : String(err));
    }
  }

  const counts = new Map<string, number>();
  for (const r of data ?? []) counts.set(r.label ?? "unclassified", (counts.get(r.label ?? "unclassified") ?? 0) + 1);

  return (
    <Page
      title="Replies"
      subtitle="Replies are classified (rules first for opt-outs, bounces and auto-replies, the model for the rest) and routed: opt-outs are suppressed everywhere, meeting requests hold a slot and open a CRM deal, 'not now' pauses the sequence."
      actions={<Button onClick={simulate}>Simulate replies to sent emails</Button>}
    >
      <ErrorNote error={error} />
      {note ? <p className="mb-4 rounded-md bg-forest-soft px-3 py-2 font-mono text-xs text-forest">{note}</p> : null}
      <div className="mb-4 flex flex-wrap gap-2">
        {[...counts.entries()].map(([name, n]) => (
          <span key={name} className="flex items-center gap-1">
            <ReplyBadge value={name === "unclassified" ? null : name} /> <span className="text-xs text-muted">{n}</span>
          </span>
        ))}
      </div>
      <div className="grid gap-5 xl:grid-cols-[1.6fr_1fr]">
        <Card title="Inbox">
          {data && data.length === 0 ? <Empty>No replies yet.</Empty> : null}
          <ul className="divide-y divide-line">
            {(data ?? []).map((r) => (
              <li key={r.id} className="py-3">
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <ReplyBadge value={r.label} />
                  {r.classification?.objection_type ? <Pill tone="red">{label(r.classification.objection_type)}</Pill> : null}
                  <span className="font-medium">{r.name ?? r.from_email}</span>
                  <span className="font-mono text-[11px] text-muted">{r.from_email}</span>
                  <span className="ml-auto text-xs text-muted">{localTime(r.received_at)}</span>
                </div>
                <div className="mt-1 text-xs text-muted">{r.subject}</div>
                <p className="mt-1 font-serif text-[14px] italic text-ink-soft">{r.body ?? "(body purged by the retention policy)"}</p>
                <div className="mt-1 text-[11px] text-muted">
                  {r.classification ? `${r.classification.source === "rules" ? "deterministic rule" : r.classification.model} · ${r.classification.reason}` : ""}
                  {r.classification?.resume_on ? ` · resume ${r.classification.resume_on}` : ""}
                </div>
                {r.actions?.length ? (
                  <ul className="mt-1 list-disc pl-5 text-xs text-forest">
                    {r.actions.map((a, i) => <li key={i}>{a}</li>)}
                  </ul>
                ) : null}
                {r.domain ? <Link className="text-[11px] text-forest underline" href={`/accounts?q=${r.domain}`}>{r.domain}</Link> : null}
              </li>
            ))}
          </ul>
        </Card>
        <Card title="Drop in a reply">
          <p className="mb-2 text-xs text-muted">Paste a reply as if it arrived in the inbox (or drop .eml files into the inbox folder the worker watches).</p>
          <input value={from} onChange={(e) => setFrom(e.target.value)} placeholder="from@company.example" className="mb-2 w-full rounded-md border border-line px-2 py-1.5 text-sm" />
          <textarea value={body} onChange={(e) => setBody(e.target.value)} placeholder="Reply text" className="mb-2 h-32 w-full rounded-md border border-line p-2 text-sm" />
          <Button onClick={manual} disabled={!from || !body}>Classify and route</Button>
        </Card>
      </div>
    </Page>
  );
}
