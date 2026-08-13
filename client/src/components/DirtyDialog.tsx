import Modal from "./Modal";
import { SpinnerLabel } from "./Spinner";
import type { PendingAction } from "../store";

export interface DirtyDialogProps {
  versionNumber: number | null;
  /** A save is in flight: all four outcomes are inert, dismissal included. */
  busy: boolean;
  pendingAction: PendingAction | null;
  /** Shown inside the dialog, so a failed save keeps its reason with the buttons. */
  error: string | null;
  onSave(): void;
  onSaveAsNewVersion(): void;
  onDiscard(): void;
  onCancel(): void;
}

/**
 * Four outcomes, so `window.confirm` (a boolean) is out.
 *
 * The layout is the hierarchy: the two ways of keeping the work stack at the top,
 * full width, primary first. Discard — the one outcome that loses data — sits
 * below a rule, at the opposite corner from the primary button, so a
 * muscle-memory click cannot land on it. Cancel is the low-risk neighbour of
 * nothing dangerous.
 */
export default function DirtyDialog({
  versionNumber,
  busy,
  pendingAction,
  error,
  onSave,
  onSaveAsNewVersion,
  onDiscard,
  onCancel,
}: DirtyDialogProps) {
  return (
    <Modal
      labelledBy="dirty-dialog-title"
      // Inert while saving: dismissing mid-save drops the pending switch, and when
      // the save lands the user gets neither the switch they asked for nor a word
      // about why.
      onDismiss={busy ? undefined : onCancel}
      className="max-w-[26rem] p-6"
    >
      <h2 id="dirty-dialog-title" className="text-lg font-semibold tracking-tight">
        Unsaved changes
      </h2>
      {/* The copy names the version: with two save buttons, "you have unsaved
          changes" does not say what Save would overwrite. */}
      <p className="mt-2 text-sm leading-relaxed text-slate-600">
        You have unsaved changes to version {versionNumber}. What would you like to do before
        switching?
      </p>

      {error && (
        <p
          role="alert"
          className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 ring-1 ring-red-100"
        >
          {error}
        </p>
      )}

      <div className="mt-5 flex flex-col gap-2">
        <button
          type="button"
          autoFocus
          aria-label="Save"
          disabled={busy}
          onClick={onSave}
          className="btn btn-primary focus-ring w-full"
        >
          <SpinnerLabel spinning={pendingAction === "save"}>Save</SpinnerLabel>
        </button>
        <button
          type="button"
          aria-label="Save as new version"
          disabled={busy}
          onClick={onSaveAsNewVersion}
          className="btn btn-secondary focus-ring w-full"
        >
          <SpinnerLabel spinning={pendingAction === "saveAsNew"}>Save as new version</SpinnerLabel>
        </button>
      </div>

      <div className="mt-5 flex items-center justify-between border-t border-slate-200 pt-4">
        <button
          type="button"
          disabled={busy}
          onClick={onDiscard}
          className="btn focus-ring border-red-200 bg-white text-red-700 hover:bg-red-50"
        >
          Discard changes
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={onCancel}
          className="btn btn-secondary focus-ring"
        >
          Cancel
        </button>
      </div>
    </Modal>
  );
}
