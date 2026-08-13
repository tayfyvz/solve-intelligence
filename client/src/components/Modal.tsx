import { useEffect, type ReactNode } from "react";

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

/** Backdrop, panel, Escape, click-outside. Two dialogs use it; nothing else does. */
export default function Modal({ labelledBy, onDismiss, className = "", children }: ModalProps) {
  useEffect(() => {
    if (!onDismiss) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onDismiss();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onDismiss]);

  return (
    <div
      role="presentation"
      onClick={() => onDismiss?.()}
      className="dialog-backdrop fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4 backdrop-blur-[2px]"
    >
      <div
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
