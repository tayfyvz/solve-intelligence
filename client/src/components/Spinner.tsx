import type { ReactNode } from "react";

export interface SpinnerProps {
  /** Size only — it is used standalone and inside a button, at two sizes. */
  className?: string;
}

export default function Spinner({ className = "h-9 w-9" }: SpinnerProps) {
  return (
    <div
      role="status"
      aria-label="Loading"
      className={`animate-spin rounded-full border-2 border-slate-200 border-b-sky-500 ${className}`}
    />
  );
}

/**
 * A button label that can spin without moving.
 *
 * Two problems at once, which is why this is one component rather than a bare
 * `{spinning && <Spinner/>}`:
 *
 * 1. A spinner that appears only while busy widens the button by ~24px mid-click,
 *    sliding its neighbour under the cursor. So the slot exists either way.
 * 2. A slot that exists only on the *left* is a real flex item, so the button's
 *    content is [16px + 8px gap + label] and `justify-content: center` centres
 *    that whole group — leaving the label itself sitting 12px right of centre.
 *    Every button in the app leaned right for this reason.
 *
 * The fix is the mirror slot: reserve the identical space on both sides and the
 * label is genuinely centred, spinning or not. Chosen over absolutely positioning
 * the spinner because that overlaps the first letter on a short label like "Save",
 * and because symmetric padding is a rule anyone can see in the markup.
 */
export function SpinnerLabel({ spinning, children }: { spinning: boolean; children: ReactNode }) {
  return (
    <>
      <span className="flex h-4 w-4 shrink-0 items-center justify-center">
        {spinning && <Spinner className="h-4 w-4" />}
      </span>
      <span className="min-w-0 truncate">{children}</span>
      <span aria-hidden="true" className="h-4 w-4 shrink-0" />
    </>
  );
}
