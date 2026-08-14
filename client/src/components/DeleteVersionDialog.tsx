import Modal from "./Modal";
import { SpinnerLabel } from "./Spinner";

export interface DeleteVersionDialogProps {
  versionNumber: number;
  versionName: string;
  /** A delete is in flight: both buttons are inert, dismissal included — same
   *  rule as DirtyDialog while a save is in flight. */
  busy: boolean;
  /** The server's 409 sentence, if the last attempt was refused. */
  error: string | null;
  onConfirm(): void;
  onCancel(): void;
}

/**
 * One destructive action behind one confirmation, built on the same Modal shell
 * as DirtyDialog. There is no generic "are you sure" component in this app —
 * with only two dialogs total, building one would be an abstraction for a
 * client of one.
 */
export default function DeleteVersionDialog({
  versionNumber,
  versionName,
  busy,
  error,
  onConfirm,
  onCancel,
}: DeleteVersionDialogProps) {
  return (
    <Modal
      labelledBy="delete-version-dialog-title"
      onDismiss={busy ? undefined : onCancel}
      className="max-w-[24rem] p-6"
    >
      <h2 id="delete-version-dialog-title" className="text-lg font-semibold tracking-tight">
        Delete version {versionNumber}?
      </h2>
      <p className="mt-2 text-sm leading-relaxed text-slate-600">
        &ldquo;{versionName}&rdquo; will be permanently deleted. This cannot be undone.
      </p>

      {error && (
        <p
          role="alert"
          className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 ring-1 ring-red-100"
        >
          {error}
        </p>
      )}

      <div className="mt-5 flex items-center justify-end gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={onCancel}
          className="btn btn-secondary focus-ring"
        >
          Cancel
        </button>
        <button
          type="button"
          autoFocus
          disabled={busy}
          onClick={onConfirm}
          className="btn focus-ring border-red-200 bg-white text-red-700 hover:bg-red-50"
        >
          <SpinnerLabel spinning={busy}>Delete version</SpinnerLabel>
        </button>
      </div>
    </Modal>
  );
}
