import Spinner from "./Spinner";
import type { VersionSummary } from "../types";

export interface VersionBarProps {
  title: string;
  versions: VersionSummary[];
  selected: number | null;
  dirty: boolean;
  /** Loading or saving: both save buttons and the version picker are inert. */
  busy: boolean;
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
  return `${date} ${time.slice(0, 5)} UTC`.trim();
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
  onSelectVersion,
  onSave,
  onSaveAsNewVersion,
}: VersionBarProps) {
  return (
    <div className="flex flex-wrap items-center gap-3 border-b border-slate-200 px-4 py-2">
      <h2 className="text-lg font-semibold">{title}</h2>

      <label htmlFor="version-select" className="text-sm text-slate-500">
        Version
      </label>
      <select
        id="version-select"
        className="rounded border border-slate-300 bg-white px-2 py-1 text-sm disabled:opacity-50"
        value={selected ?? ""}
        disabled={busy || !versions.length}
        onChange={(event) => onSelectVersion(Number(event.target.value))}
      >
        {versions.map((version) => (
          <option key={version.version_number} value={version.version_number}>
            {`Version ${version.version_number} · saved ${formatSaved(version.updated_at)}`}
          </option>
        ))}
      </select>

      {dirty && <span className="text-sm text-amber-600">Unsaved changes</span>}

      <div className="ml-auto flex items-center gap-2">
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
          className="inline-flex h-9 items-center gap-2 rounded px-4 text-sm bg-sky-600 text-white hover:bg-sky-700 disabled:opacity-50"
        >
          {busy && <Spinner className="h-4 w-4" />}
          Save
        </button>
        <button
          type="button"
          aria-label="Save as new version"
          disabled={busy || selected === null}
          onClick={onSaveAsNewVersion}
          className="inline-flex h-9 items-center gap-2 rounded border border-slate-300 bg-white px-4 text-sm hover:bg-slate-100 disabled:opacity-50"
        >
          {busy && <Spinner className="h-4 w-4" />}
          Save as new version
        </button>
      </div>
    </div>
  );
}
