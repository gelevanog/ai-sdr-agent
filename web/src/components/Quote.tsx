import { pathOf, shortDate, sourceHref } from "@/lib/format";
import type { Citation } from "@/lib/types";

export function Quote({ citation, sourceBase, compact = false }: { citation: Citation; sourceBase: string | null; compact?: boolean }) {
  return (
    <figure className={compact ? "" : "mt-1"}>
      <blockquote className="border-l-2 border-amber/60 bg-amber-soft/50 px-2 py-1 font-serif text-[13px] italic leading-snug text-ink-soft">
        “{citation.quote}”
      </blockquote>
      <figcaption className="mt-0.5 flex flex-wrap gap-x-2 text-[11px] text-muted">
        <a className="font-mono text-forest underline decoration-dotted" href={sourceHref(citation.url, sourceBase, citation.quote)} target="_blank" rel="noreferrer">
          {pathOf(citation.url)}
        </a>
        {citation.date ? <span>dated {shortDate(citation.date)}</span> : null}
        {citation.page_date ? <span>page updated {shortDate(citation.page_date)}</span> : null}
      </figcaption>
    </figure>
  );
}
