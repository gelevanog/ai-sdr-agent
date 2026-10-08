"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { Quote } from "@/components/Quote";
import { Button, Card, Empty, ErrorNote, Page, Pill, ReplyBadge, RouteBadge, StatusPill } from "@/components/ui";
import { post, useApi } from "@/lib/api";
import { label, localTime, pathOf, shortDate, sourceHref } from "@/lib/format";
import type { AccountRow, AuditRow, Profile, Qualification, ReplyRow } from "@/lib/types";

interface Dossier {
  account: AccountRow & { profile: Profile | null; qualification: Qualification | null; contact_notes: string[] | null };
  drafts: { id: number; variant: string; status: string; reviewer: string | null; created_at: string; model: string; calls: number }[];
  messages: { id: number; step: number; to_email: string; subject: string; status: string; status_reason: string | null; scheduled_at: string; sent_at: string | null; timezone: string }[];
  replies: ReplyRow[];
  pages: { url: string; fetched_at: string; purged_at: string | null; has_text: boolean }[];
  meetings: { id: number; starts_at: string; timezone: string; status: string }[];
  audit: AuditRow[];
  source_base: string | null;
}

export default function DossierPage() {
  const { id } = useParams<{ id: string }>();
  const { data, error, reload } = useApi<Dossier>(`/api/accounts/${id}`);
  const [note, setNote] = useState<string | null>(null);
  const acc = data?.account;
  const profile = acc?.profile ?? null;
  const q = acc?.qualification ?? null;
  const base = data?.source_base ?? null;

  async function job(step: string) {
    setNote(null);
    try {
      const result = await post<{ job_id: number; status: string }>(`/api/accounts/${id}/${step}`);
      setNote(`${step} queued as job ${result.job_id}; the worker runs it in the background`);
    } catch (err) {
      setNote(err instanceof Error ? err.message : String(err));
    }
  }

  async function toggleDnc() {
    if (!acc) return;
    await post(`/api/accounts/${id}/do-not-contact`, { flag: !acc.do_not_contact });
    reload();
  }

  return (
    <Page
      title={acc?.name ?? "Account"}
      subtitle={
        acc ? (
          <span className="flex flex-wrap items-center gap-2">
            <a className="font-mono text-forest underline decoration-dotted" href={sourceHref(acc.url, base)} target="_blank" rel="noreferrer">
              {acc.domain}
            </a>
            <RouteBadge route={acc.route} />
            {profile?.segment ? <Pill tone="slate">{label(profile.segment)}</Pill> : null}
            {profile?.industry ? <span>{profile.industry}</span> : null}
            {acc.do_not_contact ? <Pill tone="red">do not contact</Pill> : null}
          </span>
        ) : null
      }
      actions={
        <>
          <Button tone="secondary" onClick={() => job("research")}>Re-research</Button>
          <Button tone="secondary" onClick={() => job("qualify")}>Re-qualify</Button>
          <Button tone="secondary" onClick={() => job("draft")}>Draft again</Button>
          <Button tone="danger" onClick={toggleDnc}>{acc?.do_not_contact ? "Allow contact" : "Do not contact"}</Button>
        </>
      }
    >
      <ErrorNote error={error} />
      {note ? <p className="mb-4 rounded-md bg-forest-soft px-3 py-2 text-sm text-forest">{note}</p> : null}
      {profile && profile.injection_findings.length > 0 ? (
        <div className="mb-5 rounded-lg border border-brick/40 bg-brick-soft px-4 py-3 text-sm">
          <div className="font-semibold text-brick">Prompt injection quarantined ({profile.injection_findings.length})</div>
          {profile.injection_findings.map((f, i) => (
            <div key={i} className="mt-1 text-ink-soft">
              <span className="font-mono text-[11px] text-muted">{pathOf(f.url)}{f.hidden ? " · hidden text" : ""} · score {f.score} · {f.rules.join(", ")}</span>
              <div className="italic">“{f.text}”</div>
            </div>
          ))}
          <div className="mt-1 text-xs text-muted">This text was removed before any model saw the page, and it cannot change the score: qualification uses only cited facts.</div>
        </div>
      ) : null}
      <div className="grid gap-5 xl:grid-cols-[1.35fr_1fr]">
        <div className="space-y-5">
          <Card title="Buying signals" aside={<span className="text-xs text-muted">dates come from the page, not from the model</span>}>
            {profile?.signals.length ? (
              <ol className="space-y-3">
                {profile.signals.map((s) => (
                  <li key={s.id} id={s.id} className="rounded-md border border-line p-3">
                    <div className="flex flex-wrap items-center gap-2 text-sm">
                      <span className="font-mono text-[11px] text-muted">{s.id}</span>
                      <span className="font-medium capitalize">{label(s.type)}</span>
                      {s.detail ? <span className="text-ink-soft">{s.detail}</span> : null}
                      <span className="ml-auto flex items-center gap-2 text-xs text-muted">
                        {shortDate(s.date)}
                        {s.age_days !== null ? ` · ${s.age_days} days ago` : ""}
                        <Pill tone={s.current ? "green" : "gray"}>{s.current ? "current" : "too old"}</Pill>
                      </span>
                    </div>
                    <Quote citation={s.citation} sourceBase={base} />
                  </li>
                ))}
              </ol>
            ) : (
              <Empty>No buying signals found on the site.</Empty>
            )}
          </Card>
          <Card title="Firmographics" aside={<span className="text-xs text-muted">every fact carries its quote</span>}>
            {profile?.facts.length ? (
              <table className="w-full text-sm">
                <tbody>
                  {profile.facts.map((f) => (
                    <tr key={f.id} id={f.id} className="border-b border-line/70 align-top last:border-0">
                      <td className="w-10 py-2 font-mono text-[11px] text-muted">{f.id}</td>
                      <td className="w-32 py-2 text-muted">{label(f.field)}</td>
                      <td className="w-48 py-2 font-medium">{f.field === "segment" ? label(f.value) : f.value}</td>
                      <td className="py-2">
                        <Quote citation={f.citation} sourceBase={base} compact />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <Empty>{profile?.blocked_by_robots ? "robots.txt disallows crawling this site, so nothing was read." : "Not researched yet."}</Empty>
            )}
          </Card>
          {profile && profile.rejected.length > 0 ? (
            <Card title={`Model output rejected by validation (${profile.rejected.length})`}>
              <ul className="space-y-1 text-xs">
                {profile.rejected.map((r, i) => (
                  <li key={i}>
                    <Pill tone="red">{r.reason}</Pill> <span className="font-medium">{r.value}</span> <span className="italic text-muted">“{r.quote}”</span>
                  </li>
                ))}
              </ul>
            </Card>
          ) : null}
          <Card title="Pages crawled">
            <ul className="grid gap-1 text-xs md:grid-cols-2">
              {data?.pages.map((p) => (
                <li key={p.url} className="flex justify-between gap-2">
                  <a className="font-mono text-forest underline decoration-dotted" href={sourceHref(p.url, base)} target="_blank" rel="noreferrer">{pathOf(p.url)}</a>
                  <span className="text-muted">{p.purged_at ? "text purged (retention)" : localTime(p.fetched_at)}</span>
                </li>
              ))}
              {profile?.skipped.filter(([, r]) => r === "robots.txt").map(([u]) => (
                <li key={u} className="text-brick">{pathOf(u)}: disallowed by robots.txt, not fetched</li>
              ))}
            </ul>
          </Card>
        </div>
        <div className="space-y-5">
          <Card title="Fit score" aside={q ? <span className="text-xs text-muted">rules first{q.llm_adjustment ? ", then a bounded LLM adjustment" : ""}</span> : null}>
            {q ? (
              <>
                <div className="mb-3 flex items-baseline gap-3">
                  <span className="font-serif text-5xl font-semibold tabular-nums">{q.score}</span>
                  <span className="text-muted">/ 100</span>
                  <RouteBadge route={q.route} />
                </div>
                {q.disqualifiers.length ? (
                  <p className="mb-3 rounded bg-zinc-100 px-2 py-1 text-sm">Disqualified: {q.disqualifiers.join("; ")}</p>
                ) : null}
                <table className="w-full text-sm">
                  <tbody>
                    {q.lines.map((line, i) => (
                      <tr key={i} className="border-b border-line/70 align-top last:border-0">
                        <td className="py-1.5 pr-2 font-medium">{line.criterion}</td>
                        <td className={`w-14 py-1.5 pr-2 text-right font-mono tabular-nums ${line.points > 0 ? "text-forest" : line.points < 0 ? "text-brick" : "text-muted"}`}>
                          {line.points > 0 ? `+${line.points}` : line.points}
                          {line.max_points ? <span className="text-muted">/{line.max_points}</span> : null}
                        </td>
                        <td className="py-1.5 text-xs text-ink-soft">
                          {line.reason}{" "}
                          {line.evidence.map((e) => (
                            <a key={e} href={`#${e}`} className="ml-1 rounded bg-slate-soft px-1 font-mono text-[10px] text-slate">{e}</a>
                          ))}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {q.llm_reason && !q.llm_adjustment ? <p className="mt-2 text-xs text-muted">LLM review ({q.model}): no change. {q.llm_reason}</p> : null}
              </>
            ) : (
              <Empty>Not qualified yet.</Empty>
            )}
          </Card>
          <Card title="Contact">
            {acc?.contact ? (
              <div className="text-sm">
                <div className="font-medium">{acc.contact.name}</div>
                <div className="text-muted">{acc.contact.title} · {acc.contact.email}</div>
                <div className="mt-1 text-xs text-ink-soft">{acc.contact.reason} (source: {label(acc.contact.source)})</div>
                {acc.contact.citation ? <Quote citation={acc.contact.citation} sourceBase={base} /> : null}
              </div>
            ) : (
              <Empty>No contact with a published or supplied email.</Empty>
            )}
            {acc?.contact_notes?.length ? (
              <ul className="mt-2 list-disc pl-5 text-xs text-muted">
                {acc.contact_notes.map((n, i) => <li key={i}>{n}</li>)}
              </ul>
            ) : null}
          </Card>
          <Card title="Outreach">
            {data?.drafts.length ? (
              <ul className="space-y-1 text-sm">
                {data.drafts.map((d) => (
                  <li key={d.id} className="flex items-center gap-2">
                    <Link className="text-forest underline" href={`/review/${d.id}`}>Draft {d.id} · variant {d.variant}</Link>
                    <StatusPill status={d.status} />
                    {d.reviewer ? <span className="text-xs text-muted">by {d.reviewer}</span> : null}
                  </li>
                ))}
              </ul>
            ) : (
              <Empty>No drafts.</Empty>
            )}
            {data?.messages.length ? (
              <table className="mt-3 w-full text-xs">
                <tbody>
                  {data.messages.map((m) => (
                    <tr key={m.id} className="border-t border-line/70">
                      <td className="py-1">Step {m.step}</td>
                      <td>{localTime(m.sent_at ?? m.scheduled_at, m.timezone)}</td>
                      <td><StatusPill status={m.status} title={m.status_reason ?? undefined} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : null}
            {data?.replies.map((r) => (
              <div key={r.id} className="mt-3 rounded-md border border-line p-2 text-xs">
                <ReplyBadge value={r.label} /> <span className="italic">“{r.body}”</span>
                <ul className="mt-1 list-disc pl-4 text-muted">{(r.actions ?? []).map((a, i) => <li key={i}>{a}</li>)}</ul>
              </div>
            ))}
          </Card>
        </div>
      </div>
    </Page>
  );
}
