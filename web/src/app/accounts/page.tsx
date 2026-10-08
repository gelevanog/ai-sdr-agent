"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { Card, ErrorNote, Page, Pill, RouteBadge, ScoreBar } from "@/components/ui";
import { useApi } from "@/lib/api";
import { label } from "@/lib/format";
import type { AccountRow } from "@/lib/types";

const FILTERS = ["all", "qualified", "nurture", "disqualified"] as const;

export default function AccountsPage() {
  const { data, error } = useApi<AccountRow[]>("/api/accounts");
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("all");
  const [query, setQuery] = useState("");
  const rows = useMemo(
    () =>
      (data ?? []).filter(
        (a) => (filter === "all" || a.route === filter) && (query === "" || `${a.name} ${a.domain}`.toLowerCase().includes(query.toLowerCase())),
      ),
    [data, filter, query],
  );
  const counts = useMemo(() => {
    const c: Record<string, number> = { all: data?.length ?? 0 };
    for (const a of data ?? []) c[a.route ?? "none"] = (c[a.route ?? "none"] ?? 0) + 1;
    return c;
  }, [data]);

  return (
    <Page title="Accounts" subtitle="Every target account with its fit score, route and the buying signals found on its website. Solid chips are current signals; dashed ones are too old to act on.">
      <ErrorNote error={error} />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        {FILTERS.map((name) => (
          <button
            key={name}
            onClick={() => setFilter(name)}
            className={`rounded-full border px-3 py-1 text-sm capitalize ${filter === name ? "border-forest bg-forest text-white" : "border-line bg-white"}`}
          >
            {name} <span className="opacity-70">{counts[name] ?? 0}</span>
          </button>
        ))}
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search accounts"
          className="ml-auto w-64 rounded-md border border-line bg-white px-3 py-1.5 text-sm"
        />
      </div>
      <Card>
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase tracking-wide text-muted">
            <tr className="border-b border-line">
              <th className="py-2 pr-3">Account</th>
              <th className="pr-3">Route</th>
              <th className="pr-3">Fit score</th>
              <th className="pr-3">Signals (with citations)</th>
              <th className="pr-3">Contact</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((a) => (
              <tr key={a.id} className="border-b border-line/70 align-top hover:bg-paper">
                <td className="py-2 pr-3">
                  <Link href={`/accounts/${a.id}`} className="font-medium text-forest hover:underline">
                    {a.name}
                  </Link>
                  <div className="font-mono text-[11px] text-muted">{a.domain}</div>
                  {a.injections && a.injections.length ? <Pill tone="red">injection quarantined</Pill> : null}
                </td>
                <td className="pr-3 pt-2">
                  <RouteBadge route={a.route} />
                  {a.disqualifiers && a.disqualifiers.length ? <div className="mt-1 max-w-[180px] text-[11px] text-muted">{a.disqualifiers.join("; ")}</div> : null}
                </td>
                <td className="pr-3 pt-2.5">
                  <ScoreBar score={a.score} />
                </td>
                <td className="pr-3 pt-2">
                  <div className="flex max-w-[420px] flex-wrap gap-1">
                    {(a.signals ?? []).map((s) => (
                      <span
                        key={s.id}
                        title={`${s.citation.quote} (${s.date ?? "undated"})`}
                        className={`rounded border px-1.5 py-0.5 text-[11px] ${s.current ? "border-moss/40 bg-forest-soft text-forest" : "border-dashed border-zinc-400 text-zinc-500"}`}
                      >
                        {label(s.type)}
                        {s.detail ? `: ${s.detail}` : ""}
                      </span>
                    ))}
                    {(a.signals ?? []).length === 0 ? <span className="text-[11px] text-muted">none found</span> : null}
                  </div>
                </td>
                <td className="pr-3 pt-2 text-xs">
                  {a.contact ? (
                    <>
                      <div>{a.contact.name}</div>
                      <div className="text-muted">{a.contact.title}</div>
                    </>
                  ) : (
                    <span className="text-muted">-</span>
                  )}
                </td>
                <td className="pt-2 text-xs">
                  {label(a.status)}
                  {a.do_not_contact ? (
                    <div>
                      <Pill tone="red">do not contact</Pill>
                    </div>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </Page>
  );
}
