import { useState } from "react";

import { SpinnerLabel } from "./Spinner";

export interface InlineRenameProps {
  /** The current name, pre-filled and selected so typing replaces it. */
  value: string;
  /** Accessible name for the field, e.g. `Rename "Patent 1"`. */
  label: string;
  /** Returns an error sentence to show next to the field, or null on success. */
  onSubmit(value: string): Promise<string | null>;
  onCancel(): void;
}

/**
 * One inline editor for both lists. It renders in place of the row it is editing,
 * so the error for a duplicate name lands under the field the user is typing in
 * rather than in the banner at the other end of the app.
 *
 * Blur, deliberately, does nothing. Committing on blur races the row's own Save
 * button (the click blurs the field first, so the form would unmount before the
 * click landed), and cancelling on blur silently throws typing away. The edit
 * therefore ends only on Enter, Escape, Save or Cancel — four explicit exits.
 */
export default function InlineRename({ value, label, onSubmit, onCancel }: InlineRenameProps) {
  const [text, setText] = useState(value);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const submit = async () => {
    if (pending) return;
    const trimmed = text.trim();
    if (!trimmed) {
      setError("A name is required.");
      return;
    }
    setPending(true);
    setError(null);
    // A rejected rename keeps the row open with what the user typed: retyping a
    // 60-character name because the server said "already exists" is a punishment.
    const message = await onSubmit(trimmed);
    setPending(false);
    if (message) setError(message);
  };

  return (
    <form
      className="rounded-lg border border-slate-300 bg-white p-1.5 shadow-sm"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <input
        autoFocus
        value={text}
        aria-label={label}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? "inline-rename-error" : undefined}
        disabled={pending}
        onChange={(event) => setText(event.target.value)}
        onFocus={(event) => event.target.select()}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            // Stop it here: the same Escape would otherwise reach the dialog
            // layer's window listener.
            event.stopPropagation();
            onCancel();
          }
        }}
        className="focus-ring w-full rounded-md border border-slate-300 px-2 py-1 text-sm disabled:opacity-60"
      />

      {error && (
        <p id="inline-rename-error" role="alert" className="mt-1 text-xs leading-snug text-red-700">
          {error}
        </p>
      )}

      <div className="mt-2 flex justify-end gap-1.5">
        <button
          type="button"
          disabled={pending}
          onClick={onCancel}
          className="focus-ring rounded-md px-2 py-1 text-xs font-medium text-slate-600 transition-colors duration-150 hover:bg-slate-100 disabled:opacity-50"
        >
          Cancel
        </button>
        <button
          type="submit"
          aria-label="Save name"
          disabled={pending || !text.trim()}
          className="focus-ring flex items-center justify-center gap-1 rounded-md bg-slate-900 px-2 py-1 text-xs font-medium text-white transition-colors duration-150 hover:bg-slate-700 disabled:opacity-50"
        >
          <SpinnerLabel spinning={pending}>Save</SpinnerLabel>
        </button>
      </div>
    </form>
  );
}
