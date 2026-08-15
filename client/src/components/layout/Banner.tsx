import { useState } from "react";

import { SpinnerLabel } from "../ui/Spinner";
import type { PendingAction } from "../../store";

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
 * The buttons change with `dirty` rather than greying out. With unsaved work there are two
 * meaningful destinations, this version or a new one; with none, "Save" is a no-op, so the
 * only honest action left is a copy — the same store action under an accurate name.
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
  // Truncation is right by default, but a 500-character title has to be readable somewhere.
  // Expanded in place rather than in a tooltip touch users never see.
  const [expanded, setExpanded] = useState(false);
  const long = title.length > LONG || versionName.length > LONG;
  const showFull = expanded && long;

  return (
    // Three zones, with the identity centred on the bar. Both flanking tracks are
    // `minmax(0,1fr)`, not `1fr`: a bare `1fr` grows to its content's min-width, so the
    // two buttons on the right would out-grow the logo and drag the middle off-centre.
    <header className="appbar z-10 grid min-h-[3.25rem] w-full shrink-0 grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-3 px-3 py-1.5 text-white sm:px-4">
      <div className="flex min-w-0 shrink-0 items-center gap-2.5">
        {/* A lit tile, because at 24px on near-black a flat logo reads as a smudge. */}
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-white/10 ring-1 ring-inset ring-white/15">
          <img src="/si_logo.svg" alt="Solve Intelligence" className="h-5 w-5" />
        </span>
        <span className="text-[0.8125rem] font-semibold tracking-tight max-lg:sr-only">
          Patent Reviewer
        </span>
      </div>

      {documentId !== null ? (
        // The max-width is what stops a long title, not the `min-w-0`s: `truncate` sets
        // `nowrap`, so the h1's min-content width is the whole string and would propagate
        // into the `auto` grid track. `min-w-0` lets it shrink; `max-w-*` gives it a target.
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
                {/* "Unsaved" is the one state here about to cost the user work. The pill's
                    text already says it, so the dot is decorative. */}
                <span
                  aria-hidden="true"
                  className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-amber-300"
                />
                Unsaved changes
              </span>
            )}
          </div>

          <div className="flex min-w-0 items-baseline gap-1.5 text-[0.6875rem] leading-4 text-white/60">
            {/* The same sky as the sidebar's "Open" pill: one colour for "this is open". */}
            <span className="shrink-0 font-medium text-sky-300">
              {versionNumber === null ? "No version open" : `Version ${versionNumber}`}
            </span>
            {/* Default names are literally "Version N", so the name only earns its place
                once it says something the number does not. */}
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

      {/* Mirrors the logo column. `flex-wrap` matters at narrow widths: a `justify-end` row
          that does not fit overflows backward, into the title's column, so wrapping keeps
          the overflow growing the header's height instead. `versionNumber === null` means
          nothing is open, and a live button that can only produce an error is worse than a
          dead one. */}
      <div className="flex min-w-0 shrink-0 flex-wrap items-center justify-end gap-2">
        {documentId !== null &&
          (dirty ? (
            <>
              <button
                type="button"
                // The spinner is a labelled status node, so without an explicit name the
                // button is announced as "Loading" while it spins.
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
