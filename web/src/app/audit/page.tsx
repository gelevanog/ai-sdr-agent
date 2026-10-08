"use client";

import { useState } from "react";

import { Card, ErrorNote, Page } from "@/components/ui";
import { useApi } from "@/lib/api";
import { label, localTime } from "@/lib/format";
import type { AuditRow } from "@/lib/types";

const FILTERS = ["", "account.", "injection.", "draft.", "message.", "reply.", "suppression.", "crm.", "retention.", "settings."];

export default function AuditPage() {
  const [filter, setFilter] = useState("");
  const { data, error } = useApi<AuditRow[]>(`/api/audit?limit=400${filter ? `&action=${filter}` : ""}`);
  return (
    <Page title="Audit log" subtitle="Every decision Scout or a person made: research, quarantined injections, qualification, approvals and edits, sends, blocks and deferrals, opt-outs, CRM sync, retention purges.">
      <ErrorNote error={error} />
      <div className="mb-3 flex flex-wrap gap-1">
        {FILTERS.map((f) => (
          <button key={f} onClick={() => setFilter(f)} className={`rounded-full border px-3 py-1 text-xs ${filter === f ? "border-forest bg-forest text-white" : "border-line bg-white"}`}>
            {f ? f.replace(".", "") : "all"}
          </button>
        ))}
      </div>
      <Card>
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase tracking-wide text-muted">
            <tr className="border-b border-line">
              <th className="py-2">Time (UTC)</th>
              <th>Actor</th>
              <th>Action</th>
              <th>Entity</th>
              <th>Detail</th>
            </tr>
          </thead>
          <tbody>
            {(data ?? []).map((row) => (
              <tr key={row.id} className="border-b border-line/60 align-top">
                <td className="whitespace-nowrap py-1.5 pr-3 font-mono text-[11px] text-muted">{localTime(row.ts, "UTC")}</td>
                <td className="pr-3 text-xs">{row.actor}</td>
                <td className="pr-3 font-medium">{label(row.action)}</td>
                <td className="whitespace-nowrap pr-3 text-xs text-muted">
                  {row.entity} {row.entity_id ?? ""}
                </td>
                <td className="max-w-[560px] truncate font-mono text-[11px] text-ink-soft" title={JSON.stringify(row.detail)}>
                  {JSON.stringify(row.detail)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </Page>
  );
}
