import type { Editor } from "@tiptap/core";
import { create } from "zustand";

import {
  createVersion,
  getDocument,
  getVersion,
  listDocuments,
  toMessage,
  updateVersion,
} from "./api";
import type { DocumentSummary, VersionSummary } from "./types";

/**
 * Shared state only: something is here because two or more components need it.
 * `editor` is the reason the store exists at all — ChatPanel must apply AI output
 * to the very instance Editor owns, and they are siblings.
 *
 * Deliberately local instead: chat messages, chat input, the attached file and
 * the drag state (ChatPanel), and the pending selection behind the dirty dialog
 * (App).
 */
interface DocumentState {
  documents: DocumentSummary[];
  documentId: number | null;
  title: string;
  versions: VersionSummary[];
  versionNumber: number | null;
  /** May legitimately be "" — an emptied draft. Never test truthiness. */
  content: string | null;
  editor: Editor | null;
  dirty: boolean;
  loading: boolean;
  saving: boolean;
  error: string | null;

  loadDocuments(): Promise<void>;
  selectDocument(id: number): Promise<void>;
  selectVersion(n: number): Promise<void>;
  save(): Promise<boolean>;
  saveAsNewVersion(): Promise<boolean>;
  setEditor(e: Editor): void;
  clearEditor(e: Editor): void;
  setDirty(d: boolean): void;
  clearError(): void;
}

/**
 * Request ordering, one concept rather than a rule per action.
 *
 * The token lives at module scope: nothing renders it (in state it would
 * re-render every subscriber per request), and it must survive the editor's
 * `key` remount while staying readable from store actions, so not a ref either.
 */
let token = 0;

/** Selection actions *begin*: they change what the user is looking at. */
function beginRequest(): () => boolean {
  const mine = ++token;
  return () => mine === token;
}

/**
 * Save actions *capture*: a save that resolves after the user switched away is
 * discarded, and in particular cannot clear the new editor's dirty flag.
 */
function captureRequest(): () => boolean {
  const mine = token;
  return () => mine === token;
}

const initialState = {
  documents: [] as DocumentSummary[],
  documentId: null as number | null,
  title: "",
  versions: [] as VersionSummary[],
  versionNumber: null as number | null,
  content: null as string | null,
  editor: null as Editor | null,
  dirty: false,
  loading: false,
  saving: false,
  error: null as string | null,
};

export const useDocumentStore = create<DocumentState>((set, get) => ({
  ...initialState,

  async loadDocuments() {
    const isCurrent = beginRequest();
    set({ loading: true, error: null });
    try {
      const documents = await listDocuments();
      if (!isCurrent()) return;
      set({ documents, loading: false });
      // Without this the first paint is an empty editor column.
      if (documents.length && get().documentId === null) {
        await get().selectDocument(documents[0].id);
      }
    } catch (error) {
      // Guarded in the catch too: otherwise aborting a load that happens to 404
      // paints "not found" over the document you successfully switched to.
      if (!isCurrent()) return;
      set({ loading: false, error: toMessage(error) });
    }
  },

  async selectDocument(id) {
    const isCurrent = beginRequest();
    set({ loading: true, error: null });
    try {
      const detail = await getDocument(id);
      if (!isCurrent()) return;
      if (!detail.versions.length) {
        // Cannot happen against this server, but asking for version 0 would
        // surface as "Version 0 was not found", which reads as a bug.
        set({ loading: false, error: `"${detail.title}" has no saved versions.` });
        return;
      }
      // The highest version number is the user's newest draft.
      const latest = detail.versions.reduce(
        (highest, v) => Math.max(highest, v.version_number),
        0,
      );
      const version = await getVersion(id, latest);
      if (!isCurrent()) return;
      set({
        documentId: detail.id,
        title: detail.title,
        versions: detail.versions,
        versionNumber: version.version_number,
        content: version.content,
        dirty: false,
        loading: false,
      });
    } catch (error) {
      if (!isCurrent()) return;
      set({ loading: false, error: toMessage(error) });
    }
  },

  async selectVersion(n) {
    const id = get().documentId;
    if (id === null) return;
    const isCurrent = beginRequest();
    set({ loading: true, error: null });
    try {
      const version = await getVersion(id, n);
      if (!isCurrent()) return;
      set({
        versionNumber: version.version_number,
        content: version.content,
        dirty: false,
        loading: false,
      });
    } catch (error) {
      if (!isCurrent()) return;
      set({ loading: false, error: toMessage(error) });
    }
  },

  async save() {
    const { editor, documentId, versionNumber } = get();
    if (!editor || documentId === null || versionNumber === null) {
      set({ error: "There is no open document to save." });
      return false;
    }
    const isCurrent = captureRequest();
    set({ saving: true, error: null });
    try {
      // Updates version `n` in place — it must never create a version.
      const saved = await updateVersion(documentId, versionNumber, editor.getHTML());
      // Stale: the user has moved on, so dirty and content stay untouched.
      if (!isCurrent()) return false;
      set({
        // The dropdown shows the last-saved time; without this it stays at
        // whatever it was when the document was loaded.
        versions: get().versions.map((v) =>
          v.version_number === saved.version_number
            ? { ...v, updated_at: saved.updated_at }
            : v,
        ),
        dirty: false,
      });
      return true;
    } catch (error) {
      if (!isCurrent()) return false;
      set({ error: toMessage(error) });
      return false;
    } finally {
      // Every exit path, including the two stale ones — no later request exists
      // to clear it, and a stuck `saving` disables Save for the rest of the
      // session. Last writer wins if two saves somehow overlap; the UI only
      // allows one, and re-enabling early beats locking the user out.
      set({ saving: false });
    }
  },

  async saveAsNewVersion() {
    const { editor, documentId } = get();
    if (!editor || documentId === null) {
      set({ error: "There is no open document to save." });
      return false;
    }
    const isCurrent = captureRequest();
    set({ saving: true, error: null });
    try {
      const created = await createVersion(documentId, editor.getHTML());
      if (!isCurrent()) return false;
      set({
        versions: [
          ...get().versions,
          { version_number: created.version_number, updated_at: created.updated_at },
        ],
        versionNumber: created.version_number,
        // The server's sanitised echo is the truth after nh3 ran; moving
        // versionNumber changes the remount key, which rebuilds the editor
        // from exactly this content.
        content: created.content,
        dirty: false,
      });
      return true;
    } catch (error) {
      if (!isCurrent()) return false;
      set({ error: toMessage(error) });
      return false;
    } finally {
      set({ saving: false }); // see `save` — one clear for every exit path
    }
  },

  setEditor(editor) {
    set({ editor });
  },

  /**
   * Identity-guarded: during a key change React can commit the new child's
   * `onCreate` before the old child's `onDestroy`, and an unguarded clear would
   * null the *live* editor, silently breaking Save and the chat.
   */
  clearEditor(editor) {
    set((s) => (s.editor === editor ? { editor: null } : {}));
  },

  setDirty(dirty) {
    set({ dirty });
  },

  clearError() {
    set({ error: null });
  },
}));

/**
 * Zustand stores are module singletons; without this they leak between tests.
 * `setState(initialState)` merges — the documented `(initial, true)` replace form
 * would delete all nine actions along with the state.
 */
export function __resetStoreForTests(): void {
  token = 0;
  useDocumentStore.setState(initialState);
}
