// Audit-trail helpers (design §17.3 P13).
// Pure functions only: the page (and the test suite) derive the filter query
// and display labels from here so the contract is pinned without a DOM.

/** Turn a datetime-local value ("2026-11-06T09:30") into an ISO timestamp. */
export function toIsoLocal(value: string): string {
  if (!value) return "";
  // datetime-local gives "YYYY-MM-DDTHH:MM"; the backend wants ISO 8601 with
  // a timezone. Local time is what the operator typed, so carry the machine's
  // own offset rather than assuming UTC.
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "";
  return parsed.toISOString();
}

/** Short stable label for an audit action (for grouping/dedup). */
export function actionHead(action: string): string {
  return action.split(".")[0] || action;
}

/** "2026-11-06T09:30:00+08:00" -> "11-06 09:30" (console-friendly). */
export function formatTimestamp(timestamp: string): string {
  if (!timestamp) return "—";
  return timestamp.slice(5, 16).replace("T", " ");
}

/** JSON summary text for an entry (empty string when absent). */
export function summaryText(entry: { summary?: Record<string, unknown> }): string {
  const summary = entry.summary;
  if (!summary) return "";
  return JSON.stringify(summary);
}
