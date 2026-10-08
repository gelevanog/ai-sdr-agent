"use client";

import { useState } from "react";

import { Button, Card, ErrorNote, Page, Pill } from "@/components/ui";
import { API_URL, api, post, useApi } from "@/lib/api";
import { label, localTime } from "@/lib/format";

interface Limits {
  daily_send_cap: number;
  per_domain_daily_cap: number;
  retention_days_pages: number;
  retention_days_replies: number;
  retention_days_rejected_drafts: number;
  capture_only: boolean;
  smtp_host: string;
}

export default function SettingsPage() {
  const icp = useApi<{ yaml: string; overridden: boolean }>("/api/settings/icp");
  const limits = useApi<Limits>("/api/settings/compliance");
  const suppression = useApi<{ value: string; kind: string; reason: string; source: string; created_at: string }[]>("/api/suppression");
  const crm = useApi<{ id: number; object_type: string; properties: Record<string, unknown>; created_at: string }[]>("/api/crm/objects");
  const [value, setValue] = useState("");
  const [kind, setKind] = useState("email");
  const [note, setNote] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function saveIcp(yaml: string) {
    setErr(null);
    try {
      await api("/api/settings/icp", { method: "PUT", body: JSON.stringify({ yaml }) });
      setNote("ICP and offer saved. Re-qualify accounts to apply.");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function saveLimits(form: Record<string, number>) {
    await api("/api/settings/compliance", { method: "PUT", body: JSON.stringify(form) });
    setNote("Compliance settings saved.");
    limits.reload();
  }

  async function suppress() {
    const out = await post<{ cancelled: number }>("/api/suppression", { value, kind, reason: "added in settings" });
    setNote(`Suppressed ${value}; cancelled ${out.cancelled} scheduled message(s).`);
    setValue("");
    suppression.reload();
  }

  async function purge() {
    setNote(JSON.stringify(await post("/api/retention/purge")));
  }

  return (
    <Page title="Settings" subtitle="The ICP and offer Scout qualifies and writes against, the compliance limits, the suppression list, CRM sync and exports.">
      <ErrorNote error={err ?? icp.error} />
      {note ? <p className="mb-4 rounded-md bg-forest-soft px-3 py-2 text-sm text-forest">{note}</p> : null}
      <div className="grid gap-5 xl:grid-cols-[1.3fr_1fr]">
        <Card title="ICP and offer (YAML)" aside={icp.data?.overridden ? <Pill tone="amber">edited here</Pill> : <Pill>configs/icp.yaml</Pill>}>
          {icp.data ? <IcpEditor key={icp.data.yaml.length} initial={icp.data.yaml} onSave={saveIcp} /> : null}
        </Card>
        <div className="space-y-5">
          <Card title="Compliance" aside={<Pill tone={limits.data?.capture_only ? "green" : "red"}>{limits.data?.capture_only ? `capture only (${limits.data.smtp_host})` : "real SMTP"}</Pill>}>
            {limits.data ? <LimitsForm key={JSON.stringify(limits.data)} initial={limits.data} onSave={saveLimits} onPurge={purge} /> : null}
          </Card>
          <Card title={`Suppression list (${suppression.data?.length ?? 0})`}>
            <div className="mb-2 flex gap-2">
              <select value={kind} onChange={(e) => setKind(e.target.value)} className="rounded border border-line px-2 text-sm">
                <option value="email">address</option>
                <option value="domain">domain</option>
              </select>
              <input value={value} onChange={(e) => setValue(e.target.value)} placeholder="someone@company.example" className="flex-1 rounded border border-line px-2 py-1 text-sm" />
              <Button onClick={suppress} disabled={!value}>Suppress</Button>
            </div>
            <ul className="max-h-48 overflow-y-auto text-xs">
              {(suppression.data ?? []).map((s) => (
                <li key={s.value} className="flex justify-between border-b border-line/60 py-1">
                  <span className="font-mono">{s.value}</span>
                  <span className="text-muted">{s.reason} · {s.source} · {localTime(s.created_at)}</span>
                </li>
              ))}
            </ul>
          </Card>
          <Card title="Exports and CRM">
            <div className="mb-3 flex flex-wrap gap-2 text-sm">
              {["accounts", "contacts", "activities"].map((k) => (
                <a key={k} className="rounded border border-line bg-white px-3 py-1 text-forest hover:bg-paper" href={`${API_URL}/api/export/${k}.csv`}>
                  {k}.csv
                </a>
              ))}
            </div>
            <div className="text-xs text-muted">Mock HubSpot objects created by Scout ({crm.data?.length ?? 0}):</div>
            <ul className="mt-1 max-h-56 overflow-y-auto text-xs">
              {(crm.data ?? []).slice(0, 60).map((o) => (
                <li key={o.id} className="border-b border-line/60 py-1">
                  <Pill tone="slate">{o.object_type}</Pill>{" "}
                  {String(o.properties.name ?? o.properties.dealname ?? o.properties.email ?? o.properties.hs_email_subject ?? "")}
                  {o.properties.dealstage ? <span className="text-muted"> · {String(o.properties.dealstage)}</span> : null}
                </li>
              ))}
            </ul>
          </Card>
        </div>
      </div>
    </Page>
  );
}

function IcpEditor({ initial, onSave }: { initial: string; onSave: (yaml: string) => void }) {
  const [yaml, setYaml] = useState(initial);
  return (
    <>
      <textarea value={yaml} onChange={(e) => setYaml(e.target.value)} spellCheck={false} className="h-[560px] w-full rounded border border-line bg-zinc-50 p-3 font-mono text-[12px] leading-5" />
      <div className="mt-2 flex items-center gap-2">
        <Button onClick={() => onSave(yaml)}>Validate and save</Button>
        <span className="text-xs text-muted">Unknown keys and wrong types are rejected.</span>
      </div>
    </>
  );
}

function LimitsForm({ initial, onSave, onPurge }: { initial: Limits; onSave: (form: Record<string, number>) => void; onPurge: () => void }) {
  const [form, setForm] = useState<Record<string, number>>({
    daily_send_cap: initial.daily_send_cap,
    per_domain_daily_cap: initial.per_domain_daily_cap,
    retention_days_pages: initial.retention_days_pages,
    retention_days_replies: initial.retention_days_replies,
    retention_days_rejected_drafts: initial.retention_days_rejected_drafts,
  });
  return (
    <>
      <div className="grid grid-cols-2 gap-2 text-sm">
        {Object.entries(form).map(([key, v]) => (
          <label key={key} className="flex flex-col gap-1">
            <span className="text-xs text-muted">{label(key)}</span>
            <input type="number" min={0} value={v} onChange={(e) => setForm({ ...form, [key]: Number(e.target.value) })} className="rounded border border-line px-2 py-1" />
          </label>
        ))}
      </div>
      <div className="mt-3 flex gap-2">
        <Button onClick={() => onSave(form)}>Save</Button>
        <Button tone="secondary" onClick={onPurge}>Apply retention now</Button>
      </div>
    </>
  );
}
