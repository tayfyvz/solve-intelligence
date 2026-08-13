import { useState } from "react";

import { SpinnerLabel } from "./Spinner";
import type { PendingAction } from "../store";

export interface BannerProps {
  /** null when nothing is open: the bar is then just the product mark. */
  documentId: number | null;
  title: string;
  versionNumber: number | null;
  versionName: string;
  dirty: boolean;
  /** Loading or saving: every write here is inert. */
  busy: boolean;
  /** Which write is in flight, so only the pressed button spins. */
  pendingAction: PendingAction | null;
  onSave(): void;
  onSaveAsNewVersion(): void;
}

/** Long enough that the bar would swallow it — the threshold, not the layout. */
const LONG = 64;

/**
 * The app bar: product mark, what is open, and the write actions.
 *
 * These used to be a second header strip inside the editor pane, which spent a
 * full row of vertical space repeating a patent title while the black bar above
 * it carried a logo and nothing else. One bar now carries both, so the editor
 * pane is only the document.
 *
 * The buttons change with `dirty` rather than staying put and greying out: with
 * unsaved work there are two meaningful destinations (this version, or a new
 * one); with none, "Save" would be a no-op, so the only honest action left is
 * making a copy — the same store action under the name that describes what it
 * does from a clean state.
 */
export default function Banner({
  documentId,
  title,
  versionNumber,
  versionName,
  dirty,
  busy,
  pendingAction,
  onSave,
  onSaveAsNewVersion,
}: BannerProps) {
  // Truncation is right for the bar's default state, but the open patent has to
  // be readable *somewhere* — QA will paste a 500-character title. This expands
  // it in place, wrapped and scrollable, instead of hiding it in a tooltip that
  // touch users and screen readers never see.
  const [expanded, setExpanded] = useState(false);
  const long = title.length > LONG || versionName.length > LONG;
  const showFull = expanded && long;

  return (
    // Three zones: the logo holds the left, the document identity and its write
    // buttons travel together in the middle, and an empty third zone of equal
    // weight is what makes that middle group centred on the bar rather than
    // centred on the space the logo happens to leave over.
    <header className="z-10 grid min-h-[3.25rem] w-full shrink-0 grid-cols-[1fr_auto_1fr] items-center gap-3 bg-black px-3 py-1.5 text-white sm:px-4">
      <div className="flex shrink-0 items-center gap-2">
        <img src="/si_logo.svg" alt="Solve Intelligence" className="h-6 w-6" />
        <span className="text-[0.8125rem] font-semibold tracking-tight max-lg:sr-only">
          Patent Reviewer
        </span>
      </div>

      {documentId !== null ? (
        // The max-widths are what stop a long title, not the `min-w-0`s: `truncate`
        // sets `white-space: nowrap`, so the h1's min-content width is the whole
        // string. `min-w-0` caps each wrapper's own minimum, not its child's, so
        // without a ceiling that width propagates into the `auto` grid track and
        // runs the title off-screen through the logo. `min-w-0` on the h1 lets it
        // shrink; `max-w-*` gives it something to shrink against.
        <div className="flex min-w-0 max-w-[42rem] items-center justify-center gap-3">
          <div className="flex min-w-0 max-w-full flex-col items-center text-center">
            <div className="flex min-w-0 max-w-full items-center gap-2">
              <h1
                className={`min-w-0 text-[0.9375rem] font-semibold leading-5 tracking-tight ${
                  showFull ? "max-h-16 overflow-y-auto break-words" : "truncate"
                }`}
              >
                {title}
              </h1>
              {dirty && (
                <span
                  role="status"
                  className="shrink-0 rounded-full bg-amber-400/15 px-2 py-0.5 text-[0.6875rem] font-medium leading-4 text-amber-200 ring-1 ring-amber-300/30"
                >
                  Unsaved changes
                </span>
              )}
            </div>

            <div className="flex min-w-0 items-baseline gap-1.5 text-[0.6875rem] leading-4 text-white/60">
              <span className="shrink-0">
                {versionNumber === null ? "No version open" : `Version ${versionNumber}`}
              </span>
              {/* Default names are literally "Version N", so showing both renders
                  "Version 3  Version 3". The name only earns its place once it
                  says something the number does not. */}
              {versionNumber !== null && versionName !== `Version ${versionNumber}` && (
                <span className={showFull ? "max-h-12 overflow-y-auto break-words" : "truncate"}>
                  {versionName}
                </span>
              )}
              {long && (
                <button
                  type="button"
                  onClick={() => setExpanded((value) => !value)}
                  aria-expanded={showFull}
                  className="focus-ring shrink-0 rounded font-medium text-sky-300 underline underline-offset-2 hover:text-sky-200"
                >
                  {showFull ? "Show less" : "Show full name"}
                </button>
              )}
            </div>
          </div>

          <span aria-hidden="true" className="h-6 w-px shrink-0 bg-white/20 max-sm:hidden" />

          {/* `versionNumber === null` is "nothing is open": both writes would fail
              in the store, and a live button that can only produce an error is
              worse than a dead one. */}
          <div className="flex shrink-0 items-center gap-2">
            {dirty ? (
              <>
                <button
                  type="button"
                  // The spinner is a labelled status node; without an explicit
                  // name the button would be announced as "Loading" while it spins.
                  aria-label="Save"
                  disabled={busy || versionNumber === null}
                  onClick={onSave}
                  className="btn btn-light focus-ring"
                >
                  <SpinnerLabel spinning={pendingAction === "save"}>Save</SpinnerLabel>
                </button>
                <button
                  type="button"
                  aria-label="Save as new version"
                  disabled={busy || versionNumber === null}
                  onClick={onSaveAsNewVersion}
                  className="btn btn-outline-light focus-ring"
                >
                  <SpinnerLabel spinning={pendingAction === "saveAsNew"}>
                    Save as new version
                  </SpinnerLabel>
                </button>
              </>
            ) : (
              <button
                type="button"
                aria-label="Duplicate this version"
                disabled={busy || versionNumber === null}
                onClick={onSaveAsNewVersion}
                className="btn btn-outline-light focus-ring"
              >
                <SpinnerLabel spinning={pendingAction === "saveAsNew"}>
                  Duplicate this version
                </SpinnerLabel>
              </button>
            )}
          </div>
        </div>
      ) : (
        <span />
      )}

      {/* Empty, and load-bearing: it balances the logo's column so the middle
          zone is centred on the header. */}
      <span aria-hidden="true" />
    </header>
  );
}
