import { useEffect, useRef, useState, type ReactNode } from "react";

/** Everything Tab can reach, minus what is disabled. Enough for the dialogs this app has,
 *  and short enough to read — a tabbable-element library would be a dependency for it. */
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

export interface ModalProps {
  /** id of the heading inside `children`, for `aria-labelledby`. */
  labelledBy: string;
  /** Escape and backdrop click. `undefined` makes both inert, which is how a dialog stays
   *  put while its own save is in flight. */
  onDismiss?: () => void;
  className?: string;
  children: ReactNode;
}

/** Backdrop, panel, Escape, click-outside, a Tab trap and focus restore, so every dialog in
 *  the app gets all of it. */
export default function Modal({ labelledBy, onDismiss, className = "", children }: ModalProps) {
  const panel = useRef<HTMLDivElement>(null);
  // Read during the first render, not in an effect: `autoFocus` is applied during the
  // commit, so by the time effects run `activeElement` is already inside the dialog.
  const [trigger] = useState(() => document.activeElement as HTMLElement | null);

  // The other half of `aria-modal`: without it, closing drops focus on <body> and a
  // keyboard user restarts from the top. `isConnected`, since the trigger may be gone.
  useEffect(
    () => () => {
      if (trigger?.isConnected) trigger.focus();
    },
    [trigger],
  );

  useEffect(() => {
    if (!onDismiss) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onDismiss();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onDismiss]);

  // `aria-modal` tells a screen reader the page is inert; it does not make Tab obey.
  // Without this, four Tabs walk out into the contenteditable behind the backdrop.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Tab" || !panel.current) return;
      const stops = [...panel.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
      // Everything disabled — a save in flight. Tab has nowhere legal to go.
      if (!stops.length) {
        event.preventDefault();
        return;
      }
      const first = stops[0];
      const last = stops[stops.length - 1];
      const active = document.activeElement;
      const leaving = event.shiftKey ? active === first : active === last;
      if (leaving || !panel.current.contains(active)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <div
      role="presentation"
      onClick={() => onDismiss?.()}
      className="dialog-backdrop fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4 backdrop-blur-[2px]"
    >
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        // Or a click on any button inside bubbles to the backdrop and dismisses the
        // dialog along with the action the user just took.
        onClick={(event) => event.stopPropagation()}
        className={`dialog-panel w-full rounded-2xl border border-slate-200 bg-white shadow-2xl ${className}`}
      >
        {children}
      </div>
    </div>
  );
}
