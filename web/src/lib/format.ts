export function localTime(iso: string | null, timeZone?: string | null): string {
  if (!iso) return "-";
  const date = new Date(iso);
  try {
    return new Intl.DateTimeFormat("en-GB", {
      weekday: "short",
      day: "numeric",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
      timeZone: timeZone ?? undefined,
      timeZoneName: timeZone ? "short" : undefined,
    }).format(date);
  } catch {
    return date.toISOString();
  }
}

export function shortDate(iso: string | null): string {
  if (!iso) return "undated";
  const date = new Date(iso.length === 10 ? `${iso}T12:00:00Z` : iso);
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }).format(date);
}

export function pathOf(url: string): string {
  try {
    const parsed = new URL(url);
    return parsed.pathname === "/" ? "home page" : parsed.pathname.replace(/^\//, "");
  } catch {
    return url;
  }
}

/** Where a citation can be opened: the synthetic web's local server for demo accounts, the page itself otherwise.
 *  A text fragment (#:~:text=) scrolls supporting browsers to the quote. */
export function sourceHref(url: string, sourceBase: string | null, quote?: string): string {
  let href = url;
  if (sourceBase) {
    try {
      const parsed = new URL(url);
      const path = parsed.pathname === "/" ? "/index.html" : parsed.pathname;
      href = `${sourceBase.replace(/\/$/, "")}/${parsed.host}${path}`;
    } catch {
      href = url;
    }
  }
  if (quote) {
    const words = quote.split(/\s+/).slice(0, 8).join(" ");
    href += `#:~:text=${encodeURIComponent(words)}`;
  }
  return href;
}

export function pct(value: number | undefined | null, digits = 1): string {
  if (value === undefined || value === null || Number.isNaN(value)) return "-";
  return `${(value * 100).toFixed(digits)}%`;
}

export function label(text: string): string {
  return text.replace(/_/g, " ");
}
