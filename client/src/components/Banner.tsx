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
    // Three zones: the logo holds the left, the document identity sits alone in
    // the middle, and the write-action buttons hold the right — mirroring the
    // logo so the middle stays centred on the bar rather than on whatever space
    // the logo happens to leave over. Both flanking tracks are `minmax(0,1fr)`,
    // not bare `1fr`: a bare `1fr` track grows to fit its content's min-width,
    // so once the right side carries two real buttons instead of an empty span
    // it would out-grow the logo's column and drag the middle off-centre.
    // `minmax(0,1fr)` pins both tracks to an equal share of the leftover width
    // no matter what either side contains, which is what actually centres the
    // middle column.
    <header className="appbar z-10 grid min-h-[3.25rem] w-full shrink-0 grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-3 px-3 py-1.5 text-white sm:px-4">
      <div className="flex min-w-0 shrink-0 items-center gap-2.5">
        {/* The mark sits in its own lit tile rather than directly on the bar: at
            24px on near-black, a flat logo reads as a smudge. */}
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-white/10 ring-1 ring-inset ring-white/15">
          <img src="/si_logo.svg" alt="Solve Intelligence" className="h-5 w-5" />
        </span>
        <span className="text-[0.8125rem] font-semibold tracking-tight max-lg:sr-only">
          Patent Reviewer
        </span>
      </div>

      {documentId !== null ? (
        // The max-width is what stops a long title, not the `min-w-0`s: `truncate`
        // sets `white-space: nowrap`, so the h1's min-content width is the whole
        // string. `min-w-0` caps each wrapper's own minimum, not its child's, so
        // without a ceiling that width propagates into the `auto` grid track and
        // runs the title off-screen through the logo. `min-w-0` on the h1 lets it
        // shrink; `max-w-*` gives it something to shrink against.
        <div className="flex min-w-0 max-w-[34rem] flex-col items-center text-center">
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
                className="flex shrink-0 items-center gap-1.5 rounded-full bg-amber-400/15 px-2 py-0.5 text-[0.6875rem] font-medium leading-4 text-amber-200 ring-1 ring-amber-300/30"
              >
                {/* A live dot, because "unsaved" is the one state in the bar that
                    is about to cost the user work. aria-hidden: the pill's own
                    text already says it, and a pulsing dot has nothing to add to
                    a screen reader. */}
                <span
                  aria-hidden="true"
                  className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-amber-300"
                />
                Unsaved changes
              </span>
            )}
          </div>

          <div className="flex min-w-0 items-baseline gap-1.5 text-[0.6875rem] leading-4 text-white/60">
            {/* Sky is the same accent the sidebar uses for its "Open" pill on the
                selected version row — one colour for "this is the open thing"
                wherever that fact is shown. */}
            <span className="shrink-0 font-medium text-sky-300">
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
      ) : (
        <span />
      )}

      {/* Mirrors the logo column: `justify-end` so the buttons hug the right
          edge the way the logo hugs the left, keeping both flanking tracks the
          same shape. `flex-wrap` matters at narrow widths: a `justify-end` row
          that doesn't fit its track packs from the right and overflows
          BACKWARD, off the left edge of its own column and into the title's —
          wrapping the second button onto its own line keeps the overflow
          growing the header's height instead of bleeding sideways into
          neighbouring content. `versionNumber === null` is "nothing is open":
          both writes would fail in the store, and a live button that can only
          produce an error is worse than a dead one. */}
      <div className="flex min-w-0 shrink-0 flex-wrap items-center justify-end gap-2">
        {documentId !== null &&
          (dirty ? (
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
          ))}
      </div>
    </header>
  );
}
