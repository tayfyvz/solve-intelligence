import Spinner from "./Spinner";
import type { VersionSummary } from "../types";

export interface VersionBarProps {
  title: string;
  versions: VersionSummary[];
  selected: number | null;
  dirty: boolean;
  /** Loading or saving: both save buttons and the version picker are inert. */
  busy: boolean;
  /** Which write is in flight, if any — only that button spins. */
  pendingAction: "save" | "saveAsNew" | null;
  onSelectVersion(n: number): void;
  onSave(): void;
  onSaveAsNewVersion(): void;
}

/**
 * Timestamps arrive as SQLite's naive UTC (`2026-01-01T09:30:00`). They are shown
 * verbatim, trimmed to the minute and labelled UTC: converting a timestamp whose
 * zone we only assume would be wrong in a way nobody would notice.
 */
function formatSaved(updatedAt: string): string {
  const [date, time = ""] = updatedAt.split("T");
  const minutes = time.slice(0, 5);
  // Built by cases rather than trimmed: a date-only timestamp has no minutes, and
  // trimming the ends would still leave "2026-04-04  UTC" with a double space.
  return minutes ? `${date} ${minutes} UTC` : `${date} UTC`;
}

/**
 * The slot is rendered whether or not it spins: a spinner that appears only while
 * busy widens the button by ~24px mid-click, sliding the neighbouring button under
 * the cursor.
 */
function SpinnerSlot({ spinning }: { spinning: boolean }) {
  return (
    <span className="flex h-4 w-4 shrink-0 items-center justify-center">
      {spinning && <Spinner className="h-4 w-4" />}
    </span>
  );
}

/**
 * The two save buttons are the whole of Task 1's write side, and their symmetry is
 * the design: Save overwrites the selected version and never creates one;
 * Save as new version creates MAX+1 from the live editor buffer. Because the new
 * version captures the buffer, unsaved edits are never at risk when creating one —
 * which is why only *switching* needs the dirty dialog.
 */
export default function VersionBar({
  title,
  versions,
  selected,
  dirty,
  busy,
  pendingAction,
  onSelectVersion,
  onSave,
  onSaveAsNewVersion,
}: VersionBarProps) {
  // Presentational only: the selected version's timestamp, so the option labels can
  // stay short without losing the "when was this saved" answer.
  const current = versions.find((version) => version.version_number === selected);

  return (
    // sm:flex-nowrap with a shrinkable left block: the "Unsaved changes" badge
    // appears on the first keystroke, and while the row could wrap it pushed both
    // save buttons onto a second line — the toolbar jumping under the cursor mid-edit.
    // The title truncates instead, so the buttons never move.
    <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-3 border-b border-slate-200 px-4 py-3 sm:flex-nowrap sm:px-6">
      {/* basis-full below sm, basis-0 above: `flex-1` (basis 0) never demands enough
          width to trigger the row's flex-wrap, which crushed the title to nothing on
          a phone. Full-basis wraps the buttons to their own line there instead.
          `grow` rather than `flex-1` so the basis is not overridden by the shorthand. */}
      <div className="flex min-w-0 grow basis-full flex-col gap-2 sm:basis-0">
        <div className="flex min-w-0 items-center gap-2">
          {/* Titles are up to 500 characters; untruncated, one wraps the bar onto
              several lines and pushes the save buttons out of the header strip. */}
          <h2
            className="max-w-[22rem] truncate text-base font-semibold tracking-tight"
            title={title}
          >
            {title}
          </h2>
          {dirty && (
            <span
              role="status"
              className="shrink-0 rounded-full bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700 ring-1 ring-amber-200"
            >
              Unsaved changes
            </span>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <label
            htmlFor="version-select"
            className="text-[0.6875rem] font-semibold uppercase tracking-wide text-slate-500"
          >
            Version
          </label>
          <select
            id="version-select"
            className="focus-ring rounded-lg border border-slate-300 bg-white px-2 py-1 text-sm transition-colors duration-150 hover:border-slate-400 disabled:opacity-50"
            value={selected ?? ""}
            disabled={busy || !versions.length}
            onChange={(event) => onSelectVersion(Number(event.target.value))}
          >
            {versions.map((version) => (
              // Short label, full detail in the tooltip: the native select is sized
              // by its widest option, and "saved 2026-01-01 09:30 UTC" made it huge.
              <option
                key={version.version_number}
                value={version.version_number}
                title={`Version ${version.version_number} · saved ${formatSaved(version.updated_at)}`}
              >
                {`Version ${version.version_number}`}
              </option>
            ))}
          </select>
          {current && (
            <span className="text-xs text-slate-500">saved {formatSaved(current.updated_at)}</span>
          )}
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-2">
        {/* `selected === null` is "nothing is open" — both writes would fail in
            the store, and a live button that can only produce an error is worse
            than a dead one. */}
        <button
          type="button"
          // The spinner is a labelled status node, so without this the button's
          // accessible name would change to "Loading" the moment it goes busy.
          aria-label="Save"
          disabled={busy || selected === null}
          onClick={onSave}
          className="btn btn-primary focus-ring"
        >
          <SpinnerSlot spinning={pendingAction === "save"} />
          Save
        </button>
        <button
          type="button"
          aria-label="Save as new version"
          disabled={busy || selected === null}
          onClick={onSaveAsNewVersion}
          className="btn btn-secondary focus-ring whitespace-nowrap"
        >
          <SpinnerSlot spinning={pendingAction === "saveAsNew"} />
          Save as new version
        </button>
      </div>
    </div>
  );
}
