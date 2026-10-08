import type { Claim } from "@/lib/types";

interface Segment {
  text: string;
  claim: number | null;
}

function norm(text: string): string {
  return text.toLowerCase().replace(/[‘’]/g, "'").replace(/[“”]/g, '"').replace(/[‑–—]/g, "-");
}

/** Splits an email body into plain text and claim spans (first occurrence of each claim's text). */
export function segments(body: string, claims: Claim[]): Segment[] {
  const haystack = norm(body);
  const ranges: [number, number, number][] = [];
  claims.forEach((claim, index) => {
    const needle = norm(claim.text.trim()).replace(/[.?!]$/, "");
    if (needle.length < 3) return;
    const at = haystack.indexOf(needle);
    if (at >= 0) ranges.push([at, at + needle.length, index]);
  });
  ranges.sort((a, b) => a[0] - b[0]);
  const out: Segment[] = [];
  let cursor = 0;
  for (const [start, end, index] of ranges) {
    if (start < cursor) continue;
    if (start > cursor) out.push({ text: body.slice(cursor, start), claim: null });
    out.push({ text: body.slice(start, end), claim: index });
    cursor = end;
  }
  if (cursor < body.length) out.push({ text: body.slice(cursor), claim: null });
  return out;
}

export function ClaimText({ body, claims, active, onSelect }: { body: string; claims: Claim[]; active: number | null; onSelect: (i: number) => void }) {
  return (
    <div className="whitespace-pre-wrap font-serif text-[15px] leading-7 text-ink">
      {segments(body, claims).map((seg, i) =>
        seg.claim === null ? (
          <span key={i}>{seg.text}</span>
        ) : (
          <mark
            key={i}
            onClick={() => onSelect(seg.claim as number)}
            className={`claim claim-${claims[seg.claim].verdict} ${active === seg.claim ? "claim-active" : ""}`}
            title={`${claims[seg.claim].verdict}: ${claims[seg.claim].evidence.join(", ") || "no evidence"}`}
          >
            {seg.text}
            <sup className="ml-0.5 font-mono text-[9px] text-muted">{seg.claim + 1}</sup>
          </mark>
        ),
      )}
    </div>
  );
}
