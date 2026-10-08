"use client";

import type { ReactNode } from "react";

import { Card, Empty, ErrorNote, Kpi, Page, Pill } from "@/components/ui";
import { useApi } from "@/lib/api";
import { label, pct } from "@/lib/format";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Json = Record<string, any>;

interface Summary {
  available: boolean;
  generated?: string;
  runs?: Record<string, Json>;
  calls?: Json;
}

function Matrix({ matrix, labels }: { matrix: Record<string, Record<string, number>>; labels: string[] }) {
  return (
    <table className="text-xs">
      <thead>
        <tr>
          <th className="px-2 py-1 text-left text-muted">gold \ predicted</th>
          {labels.map((l) => (
            <th key={l} className="px-2 py-1 text-muted">{label(l)}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {labels.map((g) => (
          <tr key={g}>
            <td className="px-2 py-1 font-medium">{label(g)}</td>
            {labels.map((p) => {
              const v = matrix[g]?.[p] ?? 0;
              return (
                <td key={p} className={`px-2 py-1 text-center font-mono ${v && g === p ? "bg-forest-soft text-forest" : v ? "bg-brick-soft text-brick" : "text-zinc-300"}`}>{v}</td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Rows({ rows }: { rows: [ReactNode, ReactNode][] }) {
  return (
    <table className="w-full text-sm">
      <tbody>
        {rows.map(([k, v], i) => (
          <tr key={i} className="border-b border-line/60 last:border-0">
            <td className="py-1.5 pr-3 text-ink-soft">{k}</td>
            <td className="py-1.5 text-right font-mono tabular-nums">{v}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function EvaluationPage() {
  const { data, error } = useApi<Summary>("/api/evaluation");
  const main = data?.runs?.main ?? data?.runs?.offline ?? {};
  const acc = main.accounts;
  const drafts = main.drafts;
  const replies = main.replies;
  const repliesLlm = main.replies_no_rules;
  const claims = main.claims;
  const compliance = main.compliance ?? data?.runs?.offline?.compliance;
  const llmOnly = main.llm_only;
  const models = Object.entries(data?.runs ?? {}).filter(([name]) => name.startsWith("model"));
  const routes = ["qualified", "nurture", "disqualified"];
  const replyLabels = ["interested", "meeting_request", "not_now", "referral", "objection", "unsubscribe", "out_of_office", "bounce"];

  return (
    <Page
      title="Evaluation"
      subtitle="Measured on the synthetic web (60 fictional companies) and hand-written replies and claims, all written for this repository. Real runs use free OpenRouter models only. The numbers show the mechanisms work; they are not a promise for your market."
    >
      <ErrorNote error={error} />
      {data && !data.available ? <Empty>No results yet: run `scout eval all` and `scout eval summary`.</Empty> : null}
      {acc ? (
        <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          <Kpi label="Signal recall" value={pct(acc.research.signals.overall.recall)} hint={`precision ${pct(acc.research.signals.overall.precision)}`} />
          <Kpi label="Quotes found on the cited page" value={pct(acc.research.citations.quote_on_cited_page)} hint={`${acc.research.citations.items_with_quotes} items`} />
          <Kpi label="Route accuracy" value={pct(acc.qualification.accuracy)} hint={`rules only ${pct(acc.qualification.rules_only_accuracy)}`} />
          <Kpi label="Unsupported claims, final drafts" value={drafts ? pct(drafts.independent_audit.final_drafts.unsupported_rate) : "-"} hint={drafts ? `first drafts ${pct(drafts.independent_audit.first_drafts.unsupported_rate)}` : ""} />
          <Kpi label="Reply accuracy" value={replies ? pct(replies.accuracy) : "-"} hint={replies ? `unsubscribe recall ${pct(replies.unsubscribe_recall)}` : ""} />
          <Kpi label="Compliance checks" value={compliance ? `${compliance.passed}/${compliance.total}` : "-"} hint="no model involved" />
        </div>
      ) : null}
      <div className="grid gap-5 xl:grid-cols-2">
        {acc ? (
          <Card title={`Research (${acc.accounts} accounts, ${acc.model})`}>
            <Rows
              rows={[
                ["Signals precision / recall / F1", `${pct(acc.research.signals.overall.precision)} / ${pct(acc.research.signals.overall.recall)} / ${pct(acc.research.signals.overall.f1)}`],
                ["Freshness (current vs too old) correct", pct(acc.research.signals.freshness_accuracy)],
                ["Model quotes found on the cited page", pct(acc.research.citations.quote_on_cited_page)],
                ["... found on another crawled page (re-attributed)", pct(acc.research.citations.quote_on_another_page)],
                ["... found nowhere (rejected)", pct(acc.research.citations.quote_not_found)],
                ["Facts rejected by validation", `${pct(acc.research.facts.rejected_rate)} of ${acc.research.facts.emitted}`],
                ["Firmographics that disagree with my labels", pct(acc.research.facts.firmographic_disagreement_rate)],
                ["Injection attempts quarantined", acc.research.injection_findings],
                ["Research latency p50 / p95", `${acc.latency.research_p50} s / ${acc.latency.research_p95} s`],
                ["Model calls per account (research + judgment)", acc.calls.per_account],
              ]}
            />
          </Card>
        ) : null}
        {acc ? (
          <Card title="Qualification">
            <Rows
              rows={[
                ["Route accuracy (3 routes)", pct(acc.qualification.accuracy)],
                ["Rules only, before the LLM judgment", pct(acc.qualification.rules_only_accuracy)],
                ["'Qualified' precision / recall", `${pct(acc.qualification.qualified_vs_not.precision)} / ${pct(acc.qualification.qualified_vs_not.recall)}`],
                ["LLM-only qualification (ablation)", llmOnly ? `${pct(llmOnly.accuracy)} on ${llmOnly.accounts} accounts` : "-"],
              ]}
            />
            <div className="mt-3"><Matrix matrix={acc.qualification.confusion} labels={routes} /></div>
            <div className="mt-3 flex flex-wrap gap-1">
              {Object.entries(acc.qualification.traps as Record<string, Json>).map(([trap, t]) => (
                <Pill key={trap} tone={t.correct === t.total ? "green" : "red"}>{label(trap)} {t.correct}/{t.total}</Pill>
              ))}
            </div>
          </Card>
        ) : null}
        {drafts ? (
          <Card title={`Drafts (${drafts.accounts} accounts, ${drafts.variants} variants; audit by ${drafts.judge_model})`}>
            <Rows
              rows={[
                ["Claims unsupported, first drafts (independent audit)", `${drafts.independent_audit.first_drafts.unsupported} of ${drafts.independent_audit.first_drafts.claims} (${pct(drafts.independent_audit.first_drafts.unsupported_rate)})`],
                ["Claims unsupported, final drafts (independent audit)", `${drafts.independent_audit.final_drafts.unsupported} of ${drafts.independent_audit.final_drafts.claims} (${pct(drafts.independent_audit.final_drafts.unsupported_rate)})`],
                ["Declared claims supported, first / final (checker)", `${pct(drafts.checker_view.first_drafts.supported_by_checker)} / ${pct(drafts.checker_view.final_drafts.supported_by_checker)}`],
                ["Variants passing the checker first time", pct(drafts.first_draft_pass_rate)],
                ["Variants rewritten / trimmed", `${drafts.regenerated} / ${drafts.trimmed}`],
                ["Cited facts per first email", drafts.personalization_mean],
                ["Spam and format checks passed", pct(drafts.spam_pass_rate)],
                ["Blind preference vs a generic template", `${pct(drafts.preference_vs_template.scout_win_rate)} for Scout`],
              ]}
            />
          </Card>
        ) : null}
        {replies ? (
          <Card title={`Replies (${replies.replies} hand-written)`}>
            <Rows
              rows={[
                ["Accuracy, rules first + model", pct(replies.accuracy)],
                ["Accuracy, model only (ablation)", repliesLlm ? pct(repliesLlm.accuracy) : "-"],
                ["Unsubscribe recall / precision", `${pct(replies.unsubscribe_recall)} / ${pct(replies.unsubscribe_precision)}`],
                ["Objection type correct", pct(replies.objection_type_accuracy)],
                ["Resume date right month", pct(replies.resume_month_accuracy)],
              ]}
            />
            <div className="mt-3 overflow-x-auto"><Matrix matrix={replies.confusion} labels={replyLabels} /></div>
          </Card>
        ) : null}
        {claims ? (
          <Card title={`Claim checker on ${claims.claims} hand-written claims (${claims.unsupported} unsupported)`}>
            <Rows
              rows={[
                ["Deterministic rules only: precision / recall", `${pct(claims.rules_only.precision)} / ${pct(claims.rules_only.recall)}`],
                ["LLM verifier only", `${pct(claims.llm_only.precision)} / ${pct(claims.llm_only.recall)}`],
                ["Both (Scout's checker)", `${pct(claims.combined.precision)} / ${pct(claims.combined.recall)}`],
              ]}
            />
          </Card>
        ) : null}
        {compliance ? (
          <Card title="Compliance checks">
            <ul className="space-y-1 text-sm">
              {(compliance.checks as Json[]).map((c, i) => (
                <li key={i} className="flex gap-2">
                  <Pill tone={c.passed ? "green" : "red"}>{c.passed ? "pass" : "fail"}</Pill>
                  <span>{c.check}</span>
                </li>
              ))}
            </ul>
          </Card>
        ) : null}
        {models.length ? (
          <Card title="Free model comparison (subset)">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted">
                <tr><th className="py-1">Model</th><th>Signal F1</th><th>Route accuracy</th><th>Reply accuracy</th></tr>
              </thead>
              <tbody>
                {models.map(([name, run]) => (
                  <tr key={name} className="border-t border-line/60">
                    <td className="py-1 font-mono text-xs">{run.accounts?.model ?? name}</td>
                    <td>{run.accounts ? pct(run.accounts.research.signals.overall.f1) : "-"}</td>
                    <td>{run.accounts ? pct(run.accounts.qualification.accuracy) : "-"}</td>
                    <td>{run.replies_no_rules ? pct(run.replies_no_rules.accuracy) : run.replies ? pct(run.replies.accuracy) : "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        ) : null}
        {data?.calls ? (
          <Card title="Cloud usage">
            <Rows
              rows={[
                ["Requests to OpenRouter (all runs)", data.calls.requests],
                ["ok / retried / errors", `${data.calls.ok} / ${data.calls.retried} / ${data.calls.errors}`],
                ["Every model id ends in :free", data.calls.all_free ? "yes" : "NO"],
              ]}
            />
          </Card>
        ) : null}
      </div>
    </Page>
  );
}
