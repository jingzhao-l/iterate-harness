// Report artifact download naming/type rules. The backend owns the real
// Content-Disposition and Content-Type; these are the fallbacks used when a
// proxy strips them, so they stay pure and unit-tested.

/** A safe, browser-facing filename for a report artifact. */
export function reportDownloadName(name: string): string {
  const base = name.trim().split(/[\\/]/).pop() ?? "";
  return base || "report.html";
}

/** Fallback MIME type for a report artifact. */
export function reportContentType(name: string): string {
  return name.toLowerCase().endsWith(".csv")
    ? "text/csv; charset=utf-8"
    : "text/html; charset=utf-8";
}
