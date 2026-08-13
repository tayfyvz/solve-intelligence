import { useCallback, useEffect, useState } from "react";

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
  // Escape and click-outside are cancels, so they are gated on `busy` exactly
  // like the four buttons: dismissing the dialog mid-save drops the pending
  // switch, and when the save lands the user gets neither the switch they asked
  // for nor any explanation.
  const cancelIfIdle = useCallback(() => {
    if (!busy) onCancel();
  }, [busy, onCancel]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") cancelIfIdle();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [cancelIfIdle]);

  return (
    // Click-outside cancels; the stopPropagation keeps a click *inside* from
    // bubbling out to the backdrop and cancelling the button the user just hit.
    <div
      role="presentation"
      onClick={cancelIfIdle}
      className="dialog-backdrop fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4 backdrop-blur-[2px]"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="dirty-dialog-title"
        onClick={(event) => event.stopPropagation()}
        className="dialog-panel w-full max-w-[26rem] rounded-2xl border border-slate-200 bg-white p-6 shadow-2xl"
      >
        <h2 id="dirty-dialog-title" className="text-lg font-semibold tracking-tight">
          Unsaved changes
        </h2>
        <p className="mt-2 text-sm leading-relaxed text-slate-600">
          You have unsaved changes to version {versionNumber}. What would you like to do before
          switching?
        </p>

        {/* The dialog owns the error while it is open, so a failed save keeps the
            dialog up with the reason inside it rather than behind it. */}
        {error && (
          <p
            role="alert"
            className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 ring-1 ring-red-100"
          >
            {error}
          </p>
        )}

        <div className="mt-6 flex flex-wrap justify-end gap-2">
          <button
            type="button"
            autoFocus
            disabled={busy}
            onClick={cancelIfIdle}
            className="btn btn-secondary focus-ring"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={onDiscard}
            className="btn btn-secondary focus-ring"
          >
            Discard
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={onSaveAsNewVersion}
            className="btn btn-secondary focus-ring"
          >
            Save as new version
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={onSave}
            className="btn btn-primary focus-ring"
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
  const pendingAction = useDocumentStore((s) => s.pendingAction);
  const error = useDocumentStore((s) => s.error);

  // One value for every control: when the sidebar and the version bar disagree,
  // a save in flight leaves the patent list clickable and opens a dirty dialog
  // whose four buttons are all disabled — a modal the user cannot answer.
  const busy = loading || saving;

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

  // The dirty dialog covers switching; a reload or a closed tab is the more
  // common way out and would silently drop the same edits. Registered only while
  // dirty, because a listener that always exists prompts on every exit.
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

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
      {/* The SVG mark, not the 93KB PNG of the same logo that shipped before. */}
      <header className="z-10 flex h-14 w-full shrink-0 items-center gap-3 bg-black px-4 text-white sm:px-6">
        <img src="/si_logo.svg" alt="Solve Intelligence" className="h-7 w-7" />
        <span className="text-sm font-semibold tracking-tight">Patent Reviewer</span>
      </header>

      {/* Below `lg` the columns stack and the page scrolls, rather than crushing the
          editor to 64px and pushing the save buttons off-screen. minmax(0,1fr) stays:
          it is what stops one long unbroken claim blowing the grid out sideways. */}
      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 overflow-y-auto p-3 sm:p-4 lg:grid-cols-[12rem_minmax(0,1fr)] lg:gap-4 lg:overflow-hidden xl:grid-cols-[13rem_minmax(0,1fr)_22rem]">
        <aside className="min-h-0 lg:overflow-y-auto">
          <h2 className="px-3 pb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
            Patents
          </h2>
          <DocumentList
            documents={documents}
            selectedId={documentId}
            disabled={busy}
            onSelect={(id) => request({ kind: "document", id })}
          />
        </aside>

        {/* overflow-hidden, not overflow-y-auto: the scroll container for the
            document is the editor *inside* this cell, so the version bar and the
            error banner stay pinned instead of scrolling away with the claims. */}
        <main className="flex min-h-[26rem] flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm lg:min-h-0">
          {/* With no documents the bar is an empty title, an empty picker and two
              dead buttons — that reads as broken rather than as empty. */}
          {documents.length > 0 && (
            <VersionBar
              title={title}
              versions={versions}
              selected={versionNumber}
              dirty={dirty}
              busy={busy}
              pendingAction={pendingAction}
              onSelectVersion={(n) => request({ kind: "version", n })}
              onSave={() => void save()}
              onSaveAsNewVersion={() => void saveAsNewVersion()}
            />
          )}

          {/* While the dialog is open it renders the error itself, so the banner
              stands down rather than showing the same sentence twice. */}
          {error && !pending && (
            <div
              role="alert"
              className="flex items-start gap-3 border-b border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-700 sm:px-6"
            >
              <span className="flex-1 pt-1">{error}</span>
              <button
                type="button"
                aria-label="Dismiss error"
                onClick={clearError}
                className="focus-ring -mr-1 shrink-0 rounded-md px-2 py-1 text-base leading-none text-red-500 transition-colors duration-150 hover:bg-red-100 hover:text-red-800"
              >
                ×
              </button>
            </div>
          )}

          {loading ? (
            // Inline bars, not a full-screen blocker: a 5–15 s screen block is a
            // bug, not a loading state. The widths trace a heading and two ragged
            // paragraphs so the pane loads into roughly the shape it will hold.
            <div
              role="status"
              aria-label="Loading document"
              className="flex-1 overflow-hidden bg-slate-50 px-4"
            >
              <div className="mx-auto mt-6 max-w-[44rem] animate-pulse space-y-3 rounded-xl border border-slate-200 bg-white p-8 sm:p-14">
                <div className="h-6 w-40 rounded bg-slate-200" />
                <div className="h-4 rounded bg-slate-100" />
                <div className="h-4 rounded bg-slate-100" />
                <div className="h-4 w-4/5 rounded bg-slate-100" />
                <div className="h-4 w-2/3 rounded bg-slate-100" />
                <div className="h-2" />
                <div className="h-4 rounded bg-slate-100" />
                <div className="h-4 w-11/12 rounded bg-slate-100" />
                <div className="h-4 w-1/2 rounded bg-slate-100" />
              </div>
            </div>
          ) : documents.length === 0 ? (
            <p className="m-auto max-w-xs text-center text-sm text-slate-500">
              No patents to open. Add one on the server, then reload.
            </p>
          ) : (
            // `content !== null`, never `content &&`: "" is a legal (emptied and
            // saved) version, and a truthiness check would make it unloadable.
            content !== null && (
              <Editor
                key={`${documentId}:${versionNumber}`}
                content={content}
                // Tinted scroll container: the sheet inside it reads as paper.
                className="min-h-0 flex-1 overflow-y-auto bg-slate-50 px-4"
              />
            )
          )}
        </main>

        <aside className="min-h-[8rem] rounded-xl border border-slate-200 bg-white p-4 text-sm text-slate-500 shadow-sm lg:col-span-2 lg:min-h-0 lg:overflow-y-auto xl:col-span-1">
          AI chat — coming next
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
