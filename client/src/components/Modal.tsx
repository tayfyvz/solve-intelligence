import { useEffect, useRef, useState, type ReactNode } from "react";

/**
 * Everything Tab can reach, minus what is disabled. Enough for the two dialogs
 * this app has (buttons, inputs, textareas) and short enough to read; a full
 * tabbable-element library is a dependency neither of them needs.
 */
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

export interface ModalProps {
  /** id of the heading inside `children`, for `aria-labelledby`. */
  labelledBy: string;
  /**
   * Escape and backdrop click. Pass `undefined` to make both inert — that is how
   * a dialog stays put while its own save is in flight, instead of vanishing and
   * leaving the user with neither the result nor an explanation.
   */
  onDismiss?: () => void;
  className?: string;
  children: ReactNode;
}

/**
 * Backdrop, panel, Escape, click-outside, a Tab trap and focus restore. Two
 * dialogs use it; nothing else does — so every dialog gets all of it for free.
 */
export default function Modal({ labelledBy, onDismiss, className = "", children }: ModalProps) {
  const panel = useRef<HTMLDivElement>(null);
  // Read during the FIRST RENDER, not in an effect: React applies the dialog's
  // own `autoFocus` during the commit, before effects run, so by then
  // `document.activeElement` is already a button inside the dialog and the
  // trigger is lost.
  const [trigger] = useState(() => document.activeElement as HTMLElement | null);

  // Returning focus is the other half of `aria-modal`: without it, closing the
  // dialog drops focus on <body> and a keyboard user restarts from the top of
  // the page. `isConnected`, because the trigger may have been re-rendered away.
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

  // `aria-modal` tells a screen reader the rest of the page is inert; it does not
  // make Tab obey. Without this, four Tabs walk out of the dialog and into the
  // contenteditable behind the backdrop.
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
        // Without this a click on any button inside would bubble to the backdrop
        // and dismiss the dialog along with the action the user just took.
        onClick={(event) => event.stopPropagation()}
        className={`dialog-panel w-full rounded-2xl border border-slate-200 bg-white shadow-2xl ${className}`}
      >
        {children}
      </div>
    </div>
  );
}
