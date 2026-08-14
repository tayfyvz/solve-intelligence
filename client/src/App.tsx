import { useCallback, useEffect, useState } from "react";

import Banner from "./components/Banner";
import DirtyDialog from "./components/DirtyDialog";
import Editor from "./components/Editor";
import FindBar from "./components/FindBar";
import ImportPatentDialog from "./components/ImportPatentDialog";
import NewPatentDialog from "./components/NewPatentDialog";
import Outline from "./components/Outline";
import PatentTree from "./components/PatentTree";
import SidePanel from "./components/SidePanel";
import ChatPanel from "./components/chat/ChatPanel";
import { useDocumentStore } from "./store";

/**
 * What the user asked for next, held back by the dirty dialog. Creating a patent
 * is in here because the store opens the new patent — it replaces what is in the
 * editor exactly like clicking another row, so it goes behind the same guard.
 */
type PendingSelection =
  | { kind: "document"; id: number }
  | { kind: "version"; n: number }
  | { kind: "create" }
  | { kind: "import" };

/**
 * Store writes report failure as `false` and leave the sentence in `error`. Inline
 * editors want that sentence *next to the field*, not in the banner across the
 * app, so this hands it back to the caller and clears the shared slot.
 */
async function messageOnFailure(run: () => Promise<boolean>): Promise<string | null> {
  const ok = await run();
  if (ok) return null;
  const { error, clearError } = useDocumentStore.getState();
  // No message means the write was discarded as stale — the user moved on and a
  // later request owns the screen. Reporting "could not be saved" there would
  // claim a failure that did not happen, so say nothing.
  if (error === null) return null;
  clearError();
  return error;
}

/** `lg`, matching the Tailwind breakpoint where the columns stop stacking. */
function useIsWide(): boolean {
  const query = "(min-width: 1024px)";
  // Optional-chained because jsdom has no matchMedia: no layout query means the
  // stacked layout, which is the safe answer rather than a crash.
  const [wide, setWide] = useState(() => window.matchMedia?.(query).matches ?? false);
  useEffect(() => {
    const media = window.matchMedia?.(query);
    if (!media) return;
    const onChange = () => setWide(media.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);
  return wide;
}

/**
 * App owns the layout, the mount-time load, the window drag guards, the error
 * banner, the editor's remount key and the *only* dirty guard. Everything below
 * it takes props and renders.
 */
export default function App() {
  const documents = useDocumentStore((s) => s.documents);
  const documentsTotal = useDocumentStore((s) => s.documentsTotal);
  const documentsOffset = useDocumentStore((s) => s.documentsOffset);
  const documentsLimit = useDocumentStore((s) => s.documentsLimit);
  const documentId = useDocumentStore((s) => s.documentId);
  const title = useDocumentStore((s) => s.title);
  const versions = useDocumentStore((s) => s.versions);
  const versionsTotal = useDocumentStore((s) => s.versionsTotal);
  const versionNumber = useDocumentStore((s) => s.versionNumber);
  const versionName = useDocumentStore((s) => s.versionName);
  const content = useDocumentStore((s) => s.content);
  const dirty = useDocumentStore((s) => s.dirty);
  const loading = useDocumentStore((s) => s.loading);
  const listLoading = useDocumentStore((s) => s.listLoading);
  const saving = useDocumentStore((s) => s.saving);
  const pendingAction = useDocumentStore((s) => s.pendingAction);
  const error = useDocumentStore((s) => s.error);

  const loadDocuments = useDocumentStore((s) => s.loadDocuments);
  const loadMoreVersions = useDocumentStore((s) => s.loadMoreVersions);
  const selectDocument = useDocumentStore((s) => s.selectDocument);
  const selectVersion = useDocumentStore((s) => s.selectVersion);
  const createDocument = useDocumentStore((s) => s.createDocument);
  const renameDocument = useDocumentStore((s) => s.renameDocument);
  const renameVersion = useDocumentStore((s) => s.renameVersion);
  const deleteVersion = useDocumentStore((s) => s.deleteVersion);
  const save = useDocumentStore((s) => s.save);
  const saveAsNewVersion = useDocumentStore((s) => s.saveAsNewVersion);
  const clearError = useDocumentStore((s) => s.clearError);

  // One value for every control that changes what is in the editor: when the
  // lists and the header disagree, a save in flight leaves the patent list
  // clickable and opens a dirty dialog whose buttons are all disabled — a modal
  // the user cannot answer. Paging is *not* in here; it never touches the editor.
  const busy = loading || saving;

  // All local: nothing else in the app reads them. Stale requests are already
  // handled by the store's token, so `pending` survives a remount without help.
  const [pending, setPending] = useState<PendingSelection | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const [rightCollapsed, setRightCollapsed] = useState(false);
  const [leftWidth, setLeftWidth] = useState(280);
  const [rightWidth, setRightWidth] = useState(320);
  const isWide = useIsWide();

  useEffect(() => {
    void loadDocuments();
  }, [loadDocuments]);

  // Without these, dropping a .txt anywhere outside the drop zone makes the
  // browser navigate away to the file and destroy unsaved work. They live here
  // rather than in the chat panel, which is remounted on every version switch — a
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
      else if (target.kind === "version") void selectVersion(target.n);
      else if (target.kind === "import") setImportOpen(true);
      else setCreateOpen(true);
    },
    [selectDocument, selectVersion],
  );

  /** The one dirty guard in the client. Every selection goes through it. */
  const request = (target: PendingSelection) => {
    const alreadyThere =
      target.kind === "document"
        ? target.id === documentId
        : target.kind === "version" && target.n === versionNumber;
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
   * The held-back action proceeds the moment the document stops being dirty —
   * whichever of the two save buttons cleared it, in the dialog or in the header.
   * One rule instead of a per-button `if (ok) commit(...)`, and it gets the two
   * awkward cases right for free: a save the user cancelled mid-flight finds no
   * pending selection and switches nothing, and a save that fails leaves `dirty`
   * set, so the dialog stays open with the reason inside it.
   *
   * It leans on the store's one-writer rule: `dirty` is set only by
   * `Editor.onUpdate` and cleared only by the two save actions and the two
   * selection actions (which have already nulled `pending` via `commit`).
   */
  useEffect(() => {
    if (pending && !dirty) commit(pending);
  }, [pending, dirty, commit]);

  return (
    <div className="flex h-full w-full flex-col">
      {/* Identity, context and the write actions in one bar: the black strip used
          to carry a logo alone while a second header inside the editor pane spent
          a whole row repeating the patent title. */}
      <Banner
        documentId={documentId}
        title={title}
        versionNumber={versionNumber}
        versionName={versionName}
        dirty={dirty}
        busy={busy}
        pendingAction={pendingAction}
        onSave={() => void save()}
        onSaveAsNewVersion={() => void saveAsNewVersion()}
      />

      {/* Columns above `lg`, stacked and scrolling below it, rather than crushing
          the editor to 64px and pushing the save buttons off-screen. */}
      <div className="flex min-h-0 flex-1 flex-col gap-2.5 overflow-y-auto p-2.5 sm:p-3 lg:flex-row lg:gap-1 lg:overflow-hidden">
        <SidePanel
          side="left"
          label="Patents"
          collapsed={leftCollapsed}
          onCollapsedChange={setLeftCollapsed}
          width={leftWidth}
          onWidthChange={setLeftWidth}
          resizable={isWide}
        >
          <PatentTree
            documents={documents}
            documentsTotal={documentsTotal}
            documentsOffset={documentsOffset}
            documentsLimit={documentsLimit}
            selectedId={documentId}
            versions={versions}
            versionsTotal={versionsTotal}
            selectedVersion={versionNumber}
            selectDisabled={busy}
            listBusy={listLoading}
            onSelectDocument={(id) => request({ kind: "document", id })}
            onSelectVersion={(n) => request({ kind: "version", n })}
            onCreate={() => request({ kind: "create" })}
            onImport={() => request({ kind: "import" })}
            onRenameDocument={(id, newTitle) =>
              messageOnFailure(() => renameDocument(id, newTitle))
            }
            onRenameVersion={(n, name) => messageOnFailure(() => renameVersion(n, name))}
            onDeleteVersion={(n) => messageOnFailure(() => deleteVersion(n))}
            onPageDocuments={(offset) => void loadDocuments(offset)}
            onShowMoreVersions={() => void loadMoreVersions()}
          />
          {/* Deliberately a SIBLING of the tree, not a tab: on a 37-page patent the
              outline is how you move, and hiding it behind the patent list would make
              the one control the length of the document demands the one you cannot see. */}
          <div className="border-t border-slate-200 pt-1.5">
            <Outline />
          </div>
        </SidePanel>

        {/* overflow-hidden, not overflow-y-auto: the scroll container for the
            document is the editor *inside* this cell, so the header and the error
            banner stay pinned instead of scrolling away with the claims. */}
        <main className="panel flex min-h-[26rem] min-w-0 flex-1 flex-col overflow-hidden lg:min-h-0">
          {/* The dirty dialog renders the shared `error` itself, so the banner
              stands down rather than showing the same sentence twice. The New
              patent dialog is NOT in this condition: it renders only the message
              from its own submit (`messageOnFailure` clears the shared slot), so
              suppressing the banner while it is open hid background failures with
              nothing else to show them. */}
          {error && !pending && (
            <div
              role="alert"
              className="flex animate-[rise-in_200ms_ease-out] items-start gap-3 border-b border-red-200 bg-gradient-to-r from-red-50 to-rose-50 px-4 py-2 text-[0.8125rem] text-red-700"
            >
              <span className="flex-1 pt-0.5">{error}</span>
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

          {/* `content !== null`, never `content &&`: "" is a legal (emptied and
              saved) version, and a truthiness check would make it unloadable. */}
          {content !== null ? (
            // The editor stays mounted and visible while the next version loads —
            // dimmed and inert, not replaced by a skeleton. Switching used to be
            // three visual states (editor → skeleton → editor); now the key change
            // swaps the content in one step and the new instance fades in.
            <div
              aria-busy={loading}
              className={`flex min-h-0 flex-1 flex-col transition-opacity duration-200 ${
                loading ? "pointer-events-none opacity-40" : "opacity-100"
              }`}
            >
              {/* OUTSIDE the keyed div: remounting the find bar on every version switch
                  would clear a query the user is still working with. It reads the live
                  editor from the store, so it needs no key of its own. */}
              <FindBar />
              {/* Invariant #7: content changes ONLY by remount. This key is the
                  whole mechanism — no sync effect, no setContent. */}
              <div
                key={`${documentId}:${versionNumber}`}
                className={`desk min-h-0 flex-1 animate-[rise-in_260ms_cubic-bezier(0.16,1,0.3,1)] overflow-y-auto px-4 ${
                  leftCollapsed && rightCollapsed ? "editor-wide" : ""
                }`}
              >
                <Editor content={content} />
              </div>
            </div>
          ) : loading || listLoading ? (
            // First paint only — there is genuinely nothing to show yet. The bars
            // trace a heading and two ragged paragraphs so the pane loads into
            // roughly the shape it will hold.
            <div
              role="status"
              aria-label="Loading document"
              className="desk flex-1 overflow-hidden px-4"
            >
              {/* The sweep is an OVERLAY, not a background on this box: the bars
                  are opaque children, so a gradient painted behind them would be
                  hidden by the very thing it is meant to light up. */}
              <div className="relative mx-auto mt-6 max-w-[44rem] space-y-3 overflow-hidden rounded-xl border border-slate-200 bg-white p-8 sm:p-14">
                <div className="h-6 w-40 rounded bg-slate-200" />
                <div className="h-4 rounded bg-slate-100" />
                <div className="h-4 rounded bg-slate-100" />
                <div className="h-4 w-4/5 rounded bg-slate-100" />
                <div className="h-4 w-2/3 rounded bg-slate-100" />
                <div className="h-2" />
                <div className="h-4 rounded bg-slate-100" />
                <div className="h-4 w-11/12 rounded bg-slate-100" />
                <div className="h-4 w-1/2 rounded bg-slate-100" />
                {/* Last, so `space-y-3` does not hand the first bar a stray
                    margin-top: the utility skips only the FIRST child. */}
                <div aria-hidden="true" className="shimmer pointer-events-none absolute inset-0" />
              </div>
            </div>
          ) : (
            <div className="m-auto max-w-xs animate-[rise-in_260ms_cubic-bezier(0.16,1,0.3,1)] text-center text-[0.8125rem] text-slate-500">
              {/* An empty pane that is only a sentence reads as a failure; a mark
                  above it reads as a starting point. Decorative, so aria-hidden. */}
              <div
                aria-hidden="true"
                className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br from-indigo-500 to-sky-400 text-xl text-white shadow-lg shadow-indigo-500/25"
              >
                ✦
              </div>
              <p>No patent is open.</p>
              <button
                type="button"
                onClick={() => request({ kind: "create" })}
                className="btn btn-primary focus-ring mt-3"
              >
                Create a patent
              </button>
            </div>
          )}
        </main>

        <SidePanel
          side="right"
          label="Chat"
          collapsed={rightCollapsed}
          onCollapsedChange={setRightCollapsed}
          width={rightWidth}
          onWidthChange={setRightWidth}
          resizable={isWide}
        >
          {/* Keyed on the DOCUMENT only. Keying it on the version too would destroy
              the transcript at the instant the user accepts an AI change — including
              the "Saved as version 4" bubble they need to read. The panel clears
              itself on a *user* version switch instead (store `versionSource`). */}
          <ChatPanel
            key={documentId ?? "none"}
            documentId={documentId}
            versionNumber={versionNumber}
          />
        </SidePanel>
      </div>

      {pending && (
        <DirtyDialog
          versionNumber={versionNumber}
          busy={saving}
          pendingAction={pendingAction}
          error={error}
          onSave={() => void save()}
          onSaveAsNewVersion={() => void saveAsNewVersion()}
          onDiscard={() => commit(pending)}
          onCancel={() => setPending(null)}
        />
      )}

      {importOpen && (
        <ImportPatentDialog
          openPatentTitle={documentId === null ? null : title}
          onCancel={() => setImportOpen(false)}
          onImport={async (destination, newTitle, newContent) => {
            const message = await messageOnFailure(() =>
              destination === "document"
                ? createDocument(newTitle, newContent)
                : // No name: the server generates one from the names already taken, so
                  // importing the same file twice cannot 409 on a duplicate version name.
                  saveAsNewVersion(undefined, { content: newContent }),
            );
            if (!message) setImportOpen(false);
            return message;
          }}
        />
      )}

      {createOpen && (
        <NewPatentDialog
          onCancel={() => setCreateOpen(false)}
          onCreate={async (newTitle, newContent) => {
            const message = await messageOnFailure(() =>
              createDocument(newTitle, newContent || undefined),
            );
            if (!message) setCreateOpen(false);
            return message;
          }}
        />
      )}
    </div>
  );
}
