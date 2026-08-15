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
 * A button label that can spin without moving, rather than `{spinning && <Spinner/>}`.
 *
 * The left slot exists either way, or the button widens mid-click and slides its neighbour
 * under the cursor. The right slot mirrors it, or the label sits off-centre: a left-only slot
 * is a real flex item, so centring the group leaves the text right of centre.
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
