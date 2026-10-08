"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";

import { ClaimText } from "@/components/ClaimText";
import { Quote } from "@/components/Quote";
import { Button, Card, ErrorNote, Page, Pill, RouteBadge, StatusPill } from "@/components/ui";
import { post, useApi } from "@/lib/api";
import { label, localTime } from "@/lib/format";
import type { CheckIssue, DraftRow, Email, Profile, Qualification } from "@/lib/types";

interface DraftDetail {
  draft: DraftRow;
  account: { id: number; domain: string; name: string; url: string; score: number; route: string; timezone: string | null; source: string; qualification: Qualification | null };
  profile: Profile;
  siblings: { id: number; variant: string; status: string }[];
  feedback: { id: number; step: number; diff: string; stats: { similarity: number; chars_added: number; chars_removed: number } }[];
  messages: { id: number; step: number; status: string; scheduled_at: string; sent_at: string | null; timezone: string; status_reason: string | null }[];
  seller: { company: string; postal_address: string; from: string; offer: Record<string, string> };
  source_base: string | null;
}

function Evidence({ id, detail }: { id: string; detail: DraftDetail }) {
  const fact = detail.profile.facts.find((f) => f.id === id);
  const signal = detail.profile.signals.find((s) => s.id === id);
  if (fact || signal) {
    const citation = (fact ?? signal)!.citation;
    return (
      <div className="mt-2">
        <div className="text-xs">
          <span className="rounded bg-slate-soft px-1 font-mono text-[10px] text-slate">{id}</span>{" "}
          {fact ? `${label(fact.field)}: ${fact.value}` : `${label(signal!.type)}${signal!.detail ? `: ${signal!.detail}` : ""}`}
          {signal ? <Pill tone={signal.current ? "green" : "gray"}>{signal.current ? `current, ${signal.age_days} days old` : "too old"}</Pill> : null}
        </div>
        <Quote citation={citation} sourceBase={detail.source_base} />
      </div>
    );
  }
  const offer = detail.seller.offer[id];
  if (offer) {
    return (
      <div className="mt-2 text-xs">
        <span className="rounded bg-slate-soft px-1 font-mono text-[10px] text-slate">{id}</span> {detail.seller.company}&apos;s approved offer text:
        <div className="mt-1 border-l-2 border-slate/40 pl-2 italic text-ink-soft">{offer}</div>
      </div>
    );
  }
  return <div className="mt-2 text-xs text-brick">{id}: unknown evidence id</div>;
}

export default function ReviewPage() {
  const { id } = useParams<{ id: string }>();
  const { data, error, reload } = useApi<DraftDetail>(`/api/drafts/${id}`);
  const [step, setStep] = useState(1);
  const [active, setActive] = useState<number | null>(0);
  const [editing, setEditing] = useState(false);
  const [edits, setEdits] = useState<Record<number, { subject: string; body: string }>>({});
  const [reviewer, setReviewer] = useState("Ivan");
  const [reason, setReason] = useState("");
  const [result, setResult] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const variant = data?.draft.data;
  const emails: Email[] = useMemo(() => data?.draft.edited ?? variant?.emails ?? [], [data, variant]);
  const email = emails.find((e) => e.step === step) ?? emails[0];
  const report = variant?.report;
  const issues: CheckIssue[] = report?.issues ?? [];
  const pending = data?.draft.status === "pending";
  const contact = data?.draft.contact;

  async function approve() {
    setActionError(null);
    try {
      const payload = { reviewer, edits: Object.entries(edits).map(([s, e]) => ({ step: Number(s), ...e })) };
      const out = await post<{ scheduled: string[]; timezone: string; warnings: string[] }>(`/api/drafts/${id}/approve`, payload);
      setResult(
        `Approved. Scheduled in ${out.timezone}: ${out.scheduled.map((t) => localTime(t, out.timezone)).join(" · ")}` +
          (out.warnings.length ? `. Check: ${out.warnings.join("; ")}` : ""),
      );
      setEditing(false);
      reload();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    }
  }

  async function reject() {
    setActionError(null);
    try {
      await post(`/api/drafts/${id}/reject`, { reviewer, reason });
      setResult("Rejected.");
      reload();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    }
  }

  const current = edits[step] ?? (email ? { subject: email.subject, body: email.body } : { subject: "", body: "" });
  const claims = email?.claims ?? [];
  const activeClaim = active !== null ? claims[active] : undefined;
  const stepIssues = issues.filter((i) => i.step === step || i.step === 0);

  return (
    <Page
      title={data ? `${data.account.name}: draft ${data.draft.variant}` : "Draft"}
      subtitle={
        data ? (
          <span className="flex flex-wrap items-center gap-2">
            <Link href={`/accounts/${data.account.id}`} className="text-forest underline">dossier</Link>
            <RouteBadge route={data.account.route} />
            <span>fit {data.account.score}/100</span>
            <span>·</span>
            <span>
              to {contact?.name}, {contact?.title} &lt;{contact?.email}&gt;
            </span>
            <StatusPill status={data.draft.status} />
            <span className="text-xs">drafted by {data.draft.model} in {data.draft.calls} model call(s)</span>
          </span>
        ) : null
      }
      actions={
        data?.siblings.map((s) => (
          <Link key={s.id} href={`/review/${s.id}`} className={`rounded-md border px-3 py-1.5 text-sm ${String(s.id) === id ? "border-forest bg-forest text-white" : "border-line bg-white"}`}>
            Variant {s.variant} <span className="opacity-70">({s.status})</span>
          </Link>
        )) ?? null
      }
    >
      <ErrorNote error={error ?? actionError} />
      {result ? <p className="mb-4 rounded-md bg-forest-soft px-3 py-2 text-sm text-forest">{result}</p> : null}
      <div className="grid gap-5 xl:grid-cols-[1.25fr_1fr]">
        <div className="space-y-4">
          <Card
            title={
              <span className="flex gap-1">
                {emails.map((e) => (
                  <button
                    key={e.step}
                    onClick={() => {
                      setStep(e.step);
                      setActive(0);
                    }}
                    className={`rounded px-2.5 py-1 text-sm font-normal ${e.step === step ? "bg-forest text-white" : "bg-zinc-100 text-ink-soft"}`}
                  >
                    {e.step === 1 ? "Email 1" : `Follow-up ${e.step - 1}`}
                  </button>
                ))}
              </span>
            }
            aside={<span className="text-xs text-muted">angle: {variant?.angle}</span>}
          >
            <div className="mb-3 space-y-0.5 border-b border-line pb-3 text-sm">
              <div><span className="inline-block w-16 text-muted">From</span>{data?.seller.from}</div>
              <div><span className="inline-block w-16 text-muted">To</span>{contact?.name} &lt;{contact?.email}&gt;</div>
              <div>
                <span className="inline-block w-16 text-muted">Subject</span>
                {editing ? (
                  <input
                    className="w-[70%] rounded border border-line px-2 py-0.5"
                    value={current.subject}
                    onChange={(e) => setEdits({ ...edits, [step]: { ...current, subject: e.target.value } })}
                  />
                ) : (
                  <span className="font-medium">{email?.subject}</span>
                )}
              </div>
            </div>
            {editing ? (
              <textarea
                className="h-72 w-full rounded border border-line p-3 font-serif text-[15px] leading-7"
                value={current.body}
                onChange={(e) => setEdits({ ...edits, [step]: { ...current, body: e.target.value } })}
              />
            ) : email ? (
              <ClaimText body={email.body} claims={claims} active={active} onSelect={setActive} />
            ) : null}
            <div className="mt-4 border-t border-dashed border-line pt-2 text-[11px] leading-5 text-muted">
              -- <br />
              {data?.seller.company} · {data?.seller.postal_address}
              <br />
              Not interested? Unsubscribe in one click: [personal link added at send time]
            </div>
          </Card>
          <Card title="Review" aside={data?.draft.reviewer ? <span className="text-xs text-muted">{data.draft.status} by {data.draft.reviewer} {localTime(data.draft.reviewed_at)}</span> : null}>
            {pending ? (
              <div className="flex flex-wrap items-center gap-2">
                <input value={reviewer} onChange={(e) => setReviewer(e.target.value)} className="w-36 rounded-md border border-line px-2 py-1.5 text-sm" placeholder="Your name" />
                <Button onClick={approve}>{Object.keys(edits).length ? "Approve with my edits" : "Approve and schedule"}</Button>
                <Button tone="secondary" onClick={() => setEditing(!editing)}>{editing ? "Stop editing" : "Edit"}</Button>
                <input value={reason} onChange={(e) => setReason(e.target.value)} className="flex-1 rounded-md border border-line px-2 py-1.5 text-sm" placeholder="Reason for rejecting (kept as feedback)" />
                <Button tone="danger" onClick={reject}>Reject</Button>
              </div>
            ) : (
              <p className="text-sm text-muted">This draft is {data?.draft.status}.</p>
            )}
            {data?.messages.length ? (
              <table className="mt-3 w-full text-sm">
                <tbody>
                  {data.messages.map((m) => (
                    <tr key={m.id} className="border-t border-line/70">
                      <td className="py-1">{m.step === 1 ? "Email 1" : `Follow-up ${m.step - 1}`}</td>
                      <td>{localTime(m.sent_at ?? m.scheduled_at, m.timezone)}</td>
                      <td><StatusPill status={m.status} title={m.status_reason ?? undefined} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : null}
            {data?.feedback.map((f) => (
              <div key={f.id} className="mt-3">
                <div className="text-xs text-muted">Your edit to step {f.step}, kept as feedback: +{f.stats.chars_added} / -{f.stats.chars_removed} characters, similarity {f.stats.similarity}</div>
                <pre className="mt-1 overflow-x-auto rounded bg-zinc-50 p-2 font-mono text-[11px] leading-4">
                  {f.diff.split("\n").map((line, i) => (
                    <div key={i} className={line.startsWith("+") && !line.startsWith("+++") ? "text-forest" : line.startsWith("-") && !line.startsWith("---") ? "text-brick" : "text-muted"}>{line}</div>
                  ))}
                </pre>
              </div>
            ))}
          </Card>
        </div>
        <div className="space-y-4">
          <Card title={`Claims and sources (${claims.length})`} aside={<span className="text-xs text-muted">click a highlighted phrase</span>}>
            {claims.length === 0 ? <p className="text-sm text-muted">This email makes no personalized claims.</p> : null}
            <ol className="space-y-2">
              {claims.map((c, i) => (
                <li
                  key={i}
                  onClick={() => setActive(i)}
                  className={`cursor-pointer rounded-md border p-2 text-sm ${active === i ? "border-forest bg-forest-soft/40" : "border-line"}`}
                >
                  <div className="flex items-start gap-2">
                    <span className="font-mono text-[11px] text-muted">{i + 1}</span>
                    <span className="flex-1">{c.text}</span>
                    <Pill tone={c.verdict === "supported" ? "green" : c.verdict === "unsupported" ? "red" : "amber"}>{c.verdict}</Pill>
                  </div>
                  <div className="mt-1 pl-5 text-[11px] text-muted">
                    checked by {c.checked_by.join(" + ") || "-"} · cites {c.evidence.join(", ") || "nothing"}
                  </div>
                  {active === i && data ? (
                    <div className="pl-5">
                      {c.reason ? <p className="mt-1 text-xs text-ink-soft">Verifier: {c.reason}</p> : null}
                      {c.evidence.map((e) => <Evidence key={e} id={e} detail={data} />)}
                    </div>
                  ) : null}
                </li>
              ))}
            </ol>
            {activeClaim === undefined && claims.length ? <p className="text-xs text-muted">Select a claim to see its source.</p> : null}
          </Card>
          <Card
            title="Checks"
            aside={<Pill tone={report?.passed ? "green" : "red"}>{report?.passed ? "passed" : "blocked"}</Pill>}
          >
            <div className="mb-3 grid grid-cols-4 gap-2 text-center text-xs">
              <div className="rounded bg-zinc-50 py-2"><div className="font-serif text-lg">{report?.personalization ?? 0}</div>cited facts</div>
              <div className="rounded bg-zinc-50 py-2"><div className="font-serif text-lg">{report?.spam_score ?? 0}</div>spam score</div>
              <div className="rounded bg-zinc-50 py-2"><div className="font-serif text-lg">{report?.readability ?? 0}</div>reading ease</div>
              <div className="rounded bg-zinc-50 py-2"><div className="font-serif text-lg">{report?.words?.join(" / ")}</div>words</div>
            </div>
            {stepIssues.length === 0 ? <p className="text-sm text-forest">No issues on this email.</p> : null}
            <ul className="space-y-1 text-xs">
              {stepIssues.map((issue, i) => (
                <li key={i}>
                  <Pill tone={issue.severity === "block" ? "red" : "amber"}>{issue.severity === "block" ? "blocks" : "note"}</Pill> {label(issue.kind)}: {issue.message}
                </li>
              ))}
            </ul>
            {variant && variant.history.length > 0 ? (
              <details className="mt-3 text-xs">
                <summary className="cursor-pointer text-amber">
                  Rewritten {variant.history.length}× after the claim checker rejected earlier versions
                </summary>
                {variant.history.map((h, i) => (
                  <div key={i} className="mt-2 rounded border border-line p-2">
                    <div className="font-medium">Attempt {i + 1}{h.trimmed ? " (trimmed)" : ""}: what the checker found</div>
                    <pre className="whitespace-pre-wrap text-brick">{h.issues}</pre>
                    <pre className="mt-1 whitespace-pre-wrap font-serif text-ink-soft">{h.emails[0]?.body}</pre>
                  </div>
                ))}
              </details>
            ) : null}
          </Card>
        </div>
      </div>
    </Page>
  );
}
