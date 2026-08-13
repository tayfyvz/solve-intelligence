import { useCallback, useEffect, useState } from "react";

import Logo from "./assets/logo.png";
import DocumentList from "./components/DocumentList";
import Editor from "./components/Editor";
import VersionBar from "./components/VersionBar";
import { useDocumentStore } from "./store";

/** What the user asked to look at next, held back by the dirty dialog. */
type PendingSelection = { kind: "document"; id: number } | { kind: "version"; n: number };

interface DirtyDialogProps {
  versionNumber: number | null;
  busy: boolean;
  error: string | null;
  onSave(): void;
  onSaveAsNewVersion(): void;
  onDiscard(): void;
  onCancel(): void;
}

/**
 * Four outcomes, so `window.confirm` (a boolean) is out. File-local because a
 * shared <Dialog> abstraction for exactly one dialog is the worse trade.
 *
 * The copy names the version: with two save buttons, "you have unsaved changes"
 * without a target does not tell the user what Save would overwrite.
 */
function DirtyDialog({
  versionNumber,
  busy,
  error,
  onSave,
  onSaveAsNewVersion,
  onDiscard,
  onCancel,
}: DirtyDialogProps) {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onCancel]);

  return (
    // Click-outside cancels; the stopPropagation keeps a click *inside* from
    // bubbling out to the backdrop and cancelling the button the user just hit.
    <div
      role="presentation"
      onClick={onCancel}
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="dirty-dialog-title"
        onClick={(event) => event.stopPropagation()}
        className="w-[26rem] rounded bg-white p-6 shadow-xl"
      >
        <h2 id="dirty-dialog-title" className="text-lg font-semibold">
          Unsaved changes
        </h2>
        <p className="mt-2 text-sm text-slate-600">
          You have unsaved changes to version {versionNumber}. What would you like to do before
          switching?
        </p>

        {/* The dialog owns the error while it is open, so a failed save keeps the
            dialog up with the reason inside it rather than behind it. */}
        {error && (
          <p role="alert" className="mt-3 rounded bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </p>
        )}

        <div className="mt-5 flex flex-wrap justify-end gap-2 text-sm">
          <button
            type="button"
            autoFocus
            disabled={busy}
            onClick={onCancel}
            className="rounded border border-slate-300 px-3 py-2 hover:bg-slate-100 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={onDiscard}
            className="rounded border border-slate-300 px-3 py-2 hover:bg-slate-100 disabled:opacity-50"
          >
            Discard
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={onSaveAsNewVersion}
            className="rounded border border-slate-300 px-3 py-2 hover:bg-slate-100 disabled:opacity-50"
          >
            Save as new version
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={onSave}
            className="rounded bg-sky-600 px-3 py-2 text-white hover:bg-sky-700 disabled:opacity-50"
          >
            Save
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * App owns the grid, the mount-time load, the window drag guards, the error
 * banner, the remount key and the *only* dirty guard. Everything below it takes
 * props and renders.
 */
export default function App() {
  const documents = useDocumentStore((s) => s.documents);
  const documentId = useDocumentStore((s) => s.documentId);
  const title = useDocumentStore((s) => s.title);
  const versions = useDocumentStore((s) => s.versions);
  const versionNumber = useDocumentStore((s) => s.versionNumber);
  const content = useDocumentStore((s) => s.content);
  const dirty = useDocumentStore((s) => s.dirty);
  const loading = useDocumentStore((s) => s.loading);
  const saving = useDocumentStore((s) => s.saving);
  const error = useDocumentStore((s) => s.error);

  const loadDocuments = useDocumentStore((s) => s.loadDocuments);
  const selectDocument = useDocumentStore((s) => s.selectDocument);
  const selectVersion = useDocumentStore((s) => s.selectVersion);
  const save = useDocumentStore((s) => s.save);
  const saveAsNewVersion = useDocumentStore((s) => s.saveAsNewVersion);
  const clearError = useDocumentStore((s) => s.clearError);

  // Local, not in the store: nothing else reads it. A stale request is already
  // handled by the store's token, so this survives a remount without help.
  const [pending, setPending] = useState<PendingSelection | null>(null);

  useEffect(() => {
    void loadDocuments();
  }, [loadDocuments]);

  // Without these, dropping a .txt anywhere outside the drop zone makes the
  // browser navigate away to the file and destroy unsaved work. They live here
  // rather than in ChatPanel, which is remounted on every version switch — a
  // listener that unbinds and rebinds mid-drag is a coin flip.
  useEffect(() => {
    const prevent = (event: DragEvent) => event.preventDefault();
    window.addEventListener("dragover", prevent);
    window.addEventListener("drop", prevent);
    return () => {
      window.removeEventListener("dragover", prevent);
      window.removeEventListener("drop", prevent);
    };
  }, []);

  const commit = useCallback(
    (target: PendingSelection) => {
      setPending(null);
      if (target.kind === "document") void selectDocument(target.id);
      else void selectVersion(target.n);
    },
    [selectDocument, selectVersion],
  );

  /** The one dirty guard in the client. Every selection goes through it. */
  const request = (target: PendingSelection) => {
    const alreadyThere =
      target.kind === "document" ? target.id === documentId : target.n === versionNumber;
    if (alreadyThere) return;
    if (!dirty) {
      commit(target);
      return;
    }
    // The dialog starts clean: an error left over from an earlier request would
    // otherwise appear inside it as if the save the user has not yet asked for
    // had already failed. It is dropped rather than restored on Cancel — the
    // next failing request will say the same thing.
    clearError();
    setPending(target);
  };

  /**
   * The held-back switch proceeds the moment the document stops being dirty —
   * whichever of the two save buttons cleared it, in the dialog or in the bar.
   * One rule instead of a per-button `if (ok) commit(...)`, and it gets the two
   * awkward cases right for free: a save the user cancelled mid-flight finds no
   * pending selection and switches nothing, and a save that fails leaves `dirty`
   * set, so the dialog stays open with the reason inside it.
   *
   * It leans on the store's one-writer rule: `dirty` is set only by
   * `Editor.onUpdate` and cleared only by the two save actions and the two
   * selection actions (which have already nulled `pending` via `commit`). A
   * third thing clearing the flag would switch documents without being asked.
   */
  useEffect(() => {
    if (pending && !dirty) commit(pending);
  }, [pending, dirty, commit]);

  return (
    <div className="flex h-full w-full flex-col">
      <header className="z-10 flex h-[80px] w-full items-center justify-center bg-black text-white">
        <img src={Logo} alt="Solve Intelligence" style={{ height: "50px" }} />
      </header>

      {/* No responsive breakpoints: this is a single-viewport demo, and
          minmax(0,1fr) is what stops one long unbroken claim from blowing the
          grid out sideways. */}
      <div className="grid h-[calc(100vh-80px)] grid-cols-[13rem_minmax(0,1fr)_22rem] gap-4 p-4">
        <aside className="min-h-0 overflow-y-auto">
          <h2 className="px-3 pb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
            Patents
          </h2>
          <DocumentList
            documents={documents}
            selectedId={documentId}
            disabled={loading}
            onSelect={(id) => request({ kind: "document", id })}
          />
        </aside>

        {/* overflow-hidden, not overflow-y-auto: the scroll container for the
            document is the editor *inside* this cell, so the version bar and the
            error banner stay pinned instead of scrolling away with the claims. */}
        <main className="flex min-h-0 flex-col overflow-hidden rounded border border-slate-200 bg-white">
          <VersionBar
            title={title}
            versions={versions}
            selected={versionNumber}
            dirty={dirty}
            busy={loading || saving}
            onSelectVersion={(n) => request({ kind: "version", n })}
            onSave={() => void save()}
            onSaveAsNewVersion={() => void saveAsNewVersion()}
          />

          {/* While the dialog is open it renders the error itself, so the banner
              stands down rather than showing the same sentence twice. */}
          {error && !pending && (
            <div
              role="alert"
              className="flex items-start gap-3 border-b border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700"
            >
              <span className="flex-1">{error}</span>
              <button type="button" aria-label="Dismiss error" onClick={clearError}>
                ×
              </button>
            </div>
          )}

          {loading ? (
            // Three inline bars, not a full-screen blocker: a 5–15 s screen block
            // is a bug, not a loading state.
            <div role="status" aria-label="Loading document" className="space-y-3 px-8 py-6">
              <div className="h-4 animate-pulse rounded bg-slate-200" />
              <div className="h-4 animate-pulse rounded bg-slate-200" />
              <div className="h-4 w-2/3 animate-pulse rounded bg-slate-200" />
            </div>
          ) : (
            // `content !== null`, never `content &&`: "" is a legal (emptied and
            // saved) version, and a truthiness check would make it unloadable.
            content !== null && (
              <Editor
                key={`${documentId}:${versionNumber}`}
                content={content}
                className="min-h-0 flex-1 overflow-y-auto"
              />
            )
          )}
        </main>

        <aside className="min-h-0 overflow-y-auto rounded border border-slate-200 bg-white p-4 text-sm text-slate-500">
          Chat — added in §21
        </aside>
      </div>

      {pending && (
        <DirtyDialog
          versionNumber={versionNumber}
          busy={saving}
          error={error}
          onSave={() => void save()}
          onSaveAsNewVersion={() => void saveAsNewVersion()}
          onDiscard={() => commit(pending)}
          onCancel={() => setPending(null)}
        />
      )}
    </div>
  );
}
