/**
 * Timestamps arrive as SQLite's naive UTC ("2026-01-01T09:30:00"), with no zone suffix.
 *
 * The missing suffix means UTC, not local: `new Date("2026-01-01T09:30:00")` parses as local,
 * so a browser in UTC+13 reads every save as thirteen hours in the future. Hence the appended
 * Z, and hence the absolute form is rendered in UTC and labelled UTC.
 */
export function parseUtc(stamp: string): Date | null {
  if (typeof stamp !== "string" || !stamp) return null;
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(stamp);
  // A date-only value ("2026-04-04") is midnight UTC; anything else keeps its time.
  const withTime = stamp.includes("T") ? stamp : `${stamp}T00:00:00`;
  const date = new Date(hasZone ? stamp : `${withTime}Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** Unambiguous and zone-explicit: "2026-01-01 09:30:00 UTC". */
export function absoluteUtc(stamp: string): string {
  const date = parseUtc(stamp);
  // Unparseable: the raw string is at least a clue, "Invalid Date" is not.
  if (!date) return stamp;
  const iso = date.toISOString();
  return `${iso.slice(0, 10)} ${iso.slice(11, 19)} UTC`;
}

/** The `datetime` attribute of a <time> element — a real instant, so with the Z. */
export function machineStamp(stamp: string): string | undefined {
  return parseUtc(stamp)?.toISOString();
}

/** Coarse buckets on purpose, with the exact value one hover away. Nothing ticks: a
 *  re-render is enough, and a per-row interval timer is a leak waiting to happen. */
export function relativeTime(stamp: string, now: number = Date.now()): string {
  const date = parseUtc(stamp);
  if (!date) return stamp;

  const seconds = Math.round((now - date.getTime()) / 1000);
  // Negative means the server clock is ahead. Small skew reads as "just now"; a wild
  // value falls through to the absolute date rather than a save in the future.
  if (seconds < -60) return absoluteUtc(stamp).slice(0, 16) + " UTC";
  if (seconds < 45) return "just now";

  const minutes = Math.round(seconds / 60);
  if (minutes === 1) return "a minute ago";
  if (minutes < 60) return `${minutes} minutes ago`;

  const hours = Math.round(minutes / 60);
  if (hours === 1) return "an hour ago";
  if (hours < 24) return `${hours} hours ago`;

  const days = Math.round(hours / 24);
  if (days === 1) return "yesterday";
  if (days < 7) return `${days} days ago`;

  // Past a week, a date is more useful than "37 days ago".
  return absoluteUtc(stamp).slice(0, 10);
}
