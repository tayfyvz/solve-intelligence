import type { Editor } from "@tiptap/core";
import { create } from "zustand";

// The three write helpers are aliased because the store action that wraps each
// one has the same name; unaliased, `createDocument(...)` inside `createDocument`
// reads like recursion.
import {
  createDocument as apiCreateDocument,
  createVersion,
  deleteVersion as apiDeleteVersion,
  getDocument,
  getVersion,
  listDocuments,
  listVersions,
  renameDocument as apiRenameDocument,
  renameVersion as apiRenameVersion,
  toMessage,
  updateVersion,
} from "./api";
import type { DocumentDetail, DocumentSummary, Page, VersionRead, VersionSummary } from "./types";

/** The server's own default. Held in state as well, because the server echoes it. */
export const PAGE_SIZE = 20;

/** Which write is in flight, so only the control that started it spins. */
export type PendingAction =
  | "save"
  | "saveAsNew"
  | "createDocument"
  | "renameDocument"
  | "renameVersion"
  | "deleteVersion";

/**
 * Shared state only: something is here because two or more components need it.
 * `editor` is the reason the store exists at all — ChatPanel must apply AI output
 * to the very instance Editor owns, and they are siblings.
 *
 * Deliberately local instead: chat messages, chat input, the attached file and
 * the drag state (ChatPanel), the pending selection behind the dirty dialog
 * (App), and the text sitting in a rename/create input before it is submitted.
 */
interface DocumentState {
  // The patent list, one page of it. `total`/`offset` are here because the list
  // and its pager are separate components.
  documents: DocumentSummary[];
  documentsTotal: number;
  documentsOffset: number;
  documentsLimit: number;

  documentId: number | null;
  title: string;

  // The open patent's version list, newest first. Accumulated, not paged: page 1
  // plus every page `loadMoreVersions` has appended. `versionsTotal` is how the
  // UI knows more remain — `versions.length < versionsTotal` — so no selector
  // helper and no second flag to keep in sync.
  versions: VersionSummary[];
  versionsTotal: number;
  /** Offset of the most recent version-list fetch; the staleness guard, not a page number. */
  versionsOffset: number;
  versionsLimit: number;

  versionNumber: number | null;
  versionName: string;

  /**
   * Why `versionNumber` last changed. Written in the SAME set() as versionNumber
   * itself, so it can never disagree with it, and overwritten by EVERY subsequent
   * change, so it can never get stuck. Nothing subscribes to it — ChatPanel reads it
   * once, with getState(), inside the effect that observes the change, to tell "the
   * user moved" (clear the transcript) from "we moved, because the user accepted an
   * AI edit" (keep it).
   *
   * The one deliberate exception to the shared-state rule, justified by ATOMICITY,
   * not sharing: it is a property of a store *transition*, and recording it anywhere
   * else records it at a different moment — which is the bug, not the design.
   * A failed save never reaches the set(), so it cannot leave "ai" behind.
   */
  versionSource: "user" | "ai" | null;

  /**
   * Who created the CURRENTLY OPEN version, per the server's stored `source` field —
   * ordinary shared data, kept in sync with `versionNumber` exactly like `versionName`
   * and `content` are. Not the same field as `versionSource` above: that one is a
   * transition marker read once by ChatPanel; this one is a persisted fact about the
   * open version that survives navigation and reload. ChatPanel derives its consent
   * gate from this field, so an AI-created version stays pre-consented across a
   * refresh instead of resetting every session.
   */
  versionOrigin: "user" | "ai";

  /** May legitimately be "" — an emptied draft. Never test truthiness. */
  content: string | null;
  editor: Editor | null;
  dirty: boolean;
  /** The *editor pane* is loading: it stays mounted and dimmed, never unmounted. */
  loading: boolean;
  /** A list page is loading. Never unmounts the editor, so it never touches `dirty`. */
  listLoading: boolean;
  saving: boolean;
  pendingAction: PendingAction | null;
  error: string | null;

  loadDocuments(offset?: number): Promise<void>;
  loadVersions(offset?: number): Promise<void>;
  loadMoreVersions(): Promise<void>;
  selectDocument(id: number): Promise<void>;
  selectVersion(n: number): Promise<void>;
  createDocument(title: string, content?: string): Promise<boolean>;
  renameDocument(id: number, title: string): Promise<boolean>;
  renameVersion(n: number, name: string): Promise<boolean>;
  /**
   * Refused server-side (409) when `n` is a document's only version — the UI
   * never lets the user reach that click (see PatentTree), but the store still
   * has to report it rather than assume.
   */
  deleteVersion(n: number): Promise<boolean>;
  save(): Promise<boolean>;
  /**
   * `name` is threaded straight through to the existing `createVersion(…, name)`, so
   * the version list reads `AI: delete claim 3` instead of `Version 4`.
   * `options.content` is the explicit HTML to save, defaulting to the live buffer, so
   * keystrokes made between an AI apply and this POST are not silently folded into
   * the AI's version. Every existing caller omits both and behaves exactly as before.
   */
  saveAsNewVersion(
    name?: string,
    options?: { source?: "user" | "ai"; content?: string },
  ): Promise<boolean>;
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

/**
 * "Show more versions" is one button the user can double-click, and both clicks
 * compute the same offset — so the echoed-offset guard cannot tell them apart.
 * Module scope for the same reasons as `token`: nothing renders it (`listLoading`
 * is what the button reads), and it must not re-render every subscriber.
 */
let appending = false;

/** Selection actions *begin*: they change what the user is looking at. */
function beginRequest(): () => boolean {
  const mine = ++token;
  return () => mine === token;
}

/**
 * Everything else *captures*: a request that resolves after the user switched
 * away is discarded. For a save that means it cannot clear the new editor's
 * dirty flag; for a list page it means a selection started later wins, which is
 * right — `selectDocument` loads its own first page of versions.
 */
function captureRequest(): () => boolean {
  const mine = token;
  return () => mine === token;
}

/**
 * `api.ts` casts the response body to its declared type; a proxy, a stale deploy
 * or a 200 from the wrong route can still hand back another shape. Without these
 * checks a missing `content` would set `content: null` and leave the user staring
 * at a blank pane with no loading state and no error — a silent failure.
 */
function checkedContent(version: VersionRead): string {
  if (typeof version.content !== "string") {
    throw new Error("The server returned a version without any content.");
  }
  return version.content;
}

function checkedLatest(detail: DocumentDetail): number {
  if (typeof detail.latest_version_number !== "number") {
    throw new Error("The server returned a document without a latest version number.");
  }
  return detail.latest_version_number;
}

/**
 * The whole envelope, not just `items`: a missing `total` would render a pager
 * showing "Page 1 of NaN", which is a silent failure of a different colour.
 */
function checkedPage<T>(page: Page<T>, what: string): Page<T> {
  const ok =
    page !== null &&
    typeof page === "object" &&
    Array.isArray(page.items) &&
    typeof page.total === "number" &&
    typeof page.limit === "number" &&
    typeof page.offset === "number";
  if (!ok) throw new Error(`The server returned an unreadable list of ${what}.`);
  return page;
}

/**
 * Appending is not concatenation. A version created between two fetches shifts
 * every row down one, so the next page overlaps what we already hold; so does a
 * double-click that slips past the in-flight guard. Merging by `version_number`
 * makes a duplicate impossible rather than unlikely, and the explicit sort keeps
 * the list newest-first even if a page arrives out of order.
 */
function mergeVersions(held: VersionSummary[], incoming: VersionSummary[]): VersionSummary[] {
  const byNumber = new Map(held.map((v) => [v.version_number, v]));
  // Incoming wins on collision: it is the fresher copy of the same row.
  for (const v of incoming) byNumber.set(v.version_number, v);
  return [...byNumber.values()].sort((a, b) => b.version_number - a.version_number);
}

/** A version with no readable name would render the picker as "undefined". */
function nameOf(version: VersionRead | VersionSummary): string {
  return typeof version.name === "string" && version.name
    ? version.name
    : `Version ${version.version_number}`;
}

const initialState = {
  documents: [] as DocumentSummary[],
  documentsTotal: 0,
  documentsOffset: 0,
  documentsLimit: PAGE_SIZE,
  documentId: null as number | null,
  title: "",
  versions: [] as VersionSummary[],
  versionsTotal: 0,
  versionsOffset: 0,
  versionsLimit: PAGE_SIZE,
  versionNumber: null as number | null,
  versionName: "",
  versionSource: null as "user" | "ai" | null,
  versionOrigin: "user" as "user" | "ai",
  content: null as string | null,
  editor: null as Editor | null,
  dirty: false,
  loading: false,
  listLoading: false,
  saving: false,
  pendingAction: null as PendingAction | null,
  error: null as string | null,
};

export const useDocumentStore = create<DocumentState>((set, get) => ({
  ...initialState,

  async loadDocuments(offset = 0) {
    const isCurrent = captureRequest();
    // Optimistic: the pager highlights the page the user clicked immediately,
    // and the offset it holds is what tells a late response it is stale.
    set({ listLoading: true, documentsOffset: offset, error: null });
    try {
      const page = checkedPage(await listDocuments(get().documentsLimit, offset), "patents");
      // Two guards, one meaning: this response is no longer what is on screen.
      if (!isCurrent() || get().documentsOffset !== offset) return;
      set({
        documents: page.items,
        documentsTotal: page.total,
        documentsLimit: page.limit,
      });
      // Without this the first paint is an empty editor column. Only ever on the
      // first page load — once a patent is open, changing pages must not move it.
      if (page.items.length && get().documentId === null) {
        await get().selectDocument(page.items[0].id);
      }
    } catch (error) {
      // Guarded in the catch too: otherwise aborting a load that happens to 404
      // paints "not found" over the document you successfully switched to.
      if (!isCurrent()) return;
      set({ error: toMessage(error) });
    } finally {
      // Every exit path, the stale returns included: nothing later clears it, and
      // a stuck `listLoading` disables the pager and "Show more" for the rest of
      // the session. Last writer wins, exactly as in `save`.
      set({ listLoading: false });
    }
  },

  /** Replaces the accumulated list with one page — at offset 0, a reset to page 1. */
  async loadVersions(offset = 0) {
    const id = get().documentId;
    if (id === null) return;
    const isCurrent = captureRequest();
    set({ listLoading: true, versionsOffset: offset, error: null });
    try {
      const page = checkedPage(await listVersions(id, get().versionsLimit, offset), "versions");
      if (!isCurrent() || get().versionsOffset !== offset) return;
      // Note what is *not* set: the open version, its content and `dirty`. Loading
      // the list is not a selection — the editor keeps whatever it is holding.
      set({
        versions: page.items,
        versionsTotal: page.total,
        versionsLimit: page.limit,
      });
    } catch (error) {
      if (!isCurrent()) return;
      set({ error: toMessage(error) });
    } finally {
      set({ listLoading: false }); // see `loadDocuments`
    }
  },

  /** "Show more versions": fetches the page after what we hold and appends it. */
  async loadMoreVersions() {
    const { documentId: id, versions, versionsTotal } = get();
    // Nothing open, a fetch already running, or the whole list is already here.
    if (id === null || appending || versions.length >= versionsTotal) return;
    const offset = versions.length;
    const isCurrent = captureRequest();
    appending = true;
    set({ listLoading: true, versionsOffset: offset, error: null });
    try {
      const page = checkedPage(await listVersions(id, get().versionsLimit, offset), "versions");
      if (!isCurrent() || get().versionsOffset !== offset) return;
      // `get().versions`, not the destructured copy: whatever is held *now* is
      // what this page appends to. Same one-writer rule as `loadVersions` — the
      // editor, its content and `dirty` are none of this action's business.
      set({
        versions: mergeVersions(get().versions, page.items),
        versionsTotal: page.total,
        versionsLimit: page.limit,
      });
    } catch (error) {
      if (!isCurrent()) return;
      // The list the user is looking at stays exactly as it was; only the error
      // appears, so a failed "show more" costs nothing but the click.
      set({ error: toMessage(error) });
    } finally {
      appending = false;
      set({ listLoading: false }); // see `loadDocuments`
    }
  },

  async selectDocument(id) {
    const isCurrent = beginRequest();
    set({ loading: true, error: null });
    try {
      const detail = await getDocument(id);
      if (!isCurrent()) return;
      if (!detail.version_count) {
        // Cannot happen against this server, but asking for version 0 would
        // surface as "Version 0 was not found", which reads as a bug.
        set({ loading: false, dirty: false, error: `"${detail.title}" has no saved versions.` });
        return;
      }
      // Read before the Promise.all: a throw *inside* the array would leave the
      // other request dangling as an unhandled rejection.
      const latest = checkedLatest(detail);
      // The server tells us which version is newest; the list is only what the
      // picker shows, so the two requests are independent and go together.
      const [page, version] = await Promise.all([
        listVersions(id, get().versionsLimit, 0),
        getVersion(id, latest),
      ]);
      if (!isCurrent()) return;
      const versions = checkedPage(page, "versions");
      set({
        documentId: detail.id,
        title: detail.title,
        versions: versions.items,
        versionsTotal: versions.total,
        versionsLimit: versions.limit,
        versionsOffset: 0,
        versionNumber: version.version_number,
        versionName: nameOf(version),
        versionSource: "user",
        versionOrigin: version.source,
        content: checkedContent(version),
        dirty: false,
        loading: false,
      });
    } catch (error) {
      if (!isCurrent()) return;
      // `dirty` is deliberately NOT cleared. The editor stays MOUNTED while
      // loading (App only dims it), and on this path `documentId`/`versionNumber`
      // never moved — so the remount key never changed and the user's edited text
      // is still on screen. Clearing the flag would drop the badge, the
      // `beforeunload` guard and the dialog on the next switch, over text that is
      // still there and still unsaved.
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
        versionName: nameOf(version),
        versionSource: "user",
        versionOrigin: version.source,
        content: checkedContent(version),
        dirty: false,
        loading: false,
      });
    } catch (error) {
      if (!isCurrent()) return;
      // See `selectDocument`: a failed switch changes nothing on screen, so the
      // unsaved edits are still there and `dirty` must stay set.
      set({ loading: false, error: toMessage(error) });
    }
  },

  /**
   * Creates the patent and opens it — which discards unsaved edits in whatever
   * was open, exactly like clicking another patent, so the UI must put this
   * behind the same dirty guard.
   */
  async createDocument(title, content) {
    const trimmed = title.trim();
    if (!trimmed) {
      set({ error: "A patent needs a title." });
      return false;
    }
    const isCurrent = captureRequest();
    set({ saving: true, pendingAction: "createDocument", error: null });
    try {
      const created = await apiCreateDocument(trimmed, content ?? null);
      if (!isCurrent()) return false;
      await get().selectDocument(created.id);
      // The list is ordered by title, so the new patent may have landed on any
      // page; refreshing the page being shown is the honest, cheap answer, and
      // `total` has to be re-read regardless or the pager under-counts.
      await get().loadDocuments(get().documentsOffset);
      return true;
    } catch (error) {
      if (!isCurrent()) return false;
      // A duplicate title is a 409 whose detail is already a readable sentence
      // ("A patent called \"Widget\" already exists."). Surface it verbatim.
      set({ error: toMessage(error) });
      return false;
    } finally {
      set({ saving: false, pendingAction: null });
    }
  },

  async renameDocument(id, title) {
    const trimmed = title.trim();
    if (!trimmed) {
      set({ error: "A patent needs a title." });
      return false;
    }
    const isCurrent = captureRequest();
    set({ saving: true, pendingAction: "renameDocument", error: null });
    try {
      const updated = await apiRenameDocument(id, trimmed);
      if (!isCurrent()) return false;
      // The header shows the open patent's title; if that is the one that moved,
      // it has to change with it.
      if (get().documentId === updated.id) set({ title: updated.title });
      // A title change reorders the (title-ordered) list and can move the row to
      // another page, so refetch rather than patching the row in place.
      await get().loadDocuments(get().documentsOffset);
      return true;
    } catch (error) {
      if (!isCurrent()) return false;
      set({ error: toMessage(error) });
      return false;
    } finally {
      set({ saving: false, pendingAction: null });
    }
  },

  /**
   * Renames a version of the open patent. Unlike patents, the version list is
   * ordered by number, so a rename cannot reorder or re-page it — patching the
   * row we hold is correct and avoids a refetch flicker.
   */
  async renameVersion(n, name) {
    const id = get().documentId;
    if (id === null) {
      set({ error: "There is no open patent." });
      return false;
    }
    const trimmed = name.trim();
    if (!trimmed) {
      set({ error: "A version needs a name." });
      return false;
    }
    const isCurrent = captureRequest();
    set({ saving: true, pendingAction: "renameVersion", error: null });
    try {
      const updated = await apiRenameVersion(id, n, trimmed);
      if (!isCurrent()) return false;
      set({
        versions: get().versions.map((v) =>
          v.version_number === updated.version_number ? { ...v, name: updated.name } : v,
        ),
        // Renaming the version you are looking at must change what the bar says.
        // Nothing else moves: PATCH never touches content, so `dirty` and the
        // editor are none of this action's business.
        ...(get().versionNumber === updated.version_number
          ? { versionName: nameOf(updated) }
          : null),
      });
      return true;
    } catch (error) {
      if (!isCurrent()) return false;
      set({ error: toMessage(error) });
      return false;
    } finally {
      set({ saving: false, pendingAction: null });
    }
  },

  /**
   * Deletes one version of the open patent. `captureRequest`, like every other
   * write here — but when the deleted version is the one open in the editor,
   * it hands off to `selectDocument`, which picks the newest survivor exactly
   * the way the app already picks a version the first time a patent opens.
   * That inner call owns its own token bump; this action's job ends once it
   * has kicked it off.
   */
  async deleteVersion(n) {
    const id = get().documentId;
    if (id === null) {
      set({ error: "There is no open patent." });
      return false;
    }
    const isCurrent = captureRequest();
    set({ saving: true, pendingAction: "deleteVersion", error: null });
    try {
      await apiDeleteVersion(id, n);
      if (!isCurrent()) return false;
      const wasOpen = get().versionNumber === n;
      set({
        versions: get().versions.filter((v) => v.version_number !== n),
        versionsTotal: get().versionsTotal - 1,
        // The patent row shows a version count too; without this it keeps
        // saying the old total next to a list that now shows one fewer.
        documents: get().documents.map((d) =>
          d.id === id ? { ...d, version_count: d.version_count - 1 } : d,
        ),
      });
      // The deleted version's content is gone; there is nothing left on screen
      // to call dirty. Re-opening the patent is exactly what a first load does
      // — it asks the server which version is newest now and opens that one.
      if (wasOpen) await get().selectDocument(id);
      return true;
    } catch (error) {
      if (!isCurrent()) return false;
      set({ error: toMessage(error) });
      return false;
    } finally {
      set({ saving: false, pendingAction: null });
    }
  },

  async save() {
    const { editor, documentId, versionNumber } = get();
    if (!editor || documentId === null || versionNumber === null) {
      set({ error: "There is no open document to save." });
      return false;
    }
    const isCurrent = captureRequest();
    set({ saving: true, pendingAction: "save", error: null });
    try {
      // Captured BEFORE the request, exactly as ChatPanel does around the AI
      // round-trip: these are the bytes the server now holds, and the reference
      // point for the drift check below.
      const sent = editor.getHTML();
      // Updates version `n` in place — it must never create a version.
      const saved = await updateVersion(documentId, versionNumber, sent);
      // Stale: the user has moved on, so dirty and content stay untouched.
      if (!isCurrent()) return false;
      set({
        // The picker shows the last-saved time; without this it stays at
        // whatever it was when the document was loaded. Nothing reorders and no
        // row appears, so there is nothing to refetch.
        versions: get().versions.map((v) =>
          v.version_number === saved.version_number
            ? { ...v, updated_at: saved.updated_at }
            : v,
        ),
        // THE DRIFT GUARD, same rule as ChatPanel's. Keystrokes typed while the
        // PUT was in flight are NOT on the server; clearing `dirty` over them
        // would drop the badge, the `beforeunload` guard and the dirty dialog, and
        // the next version switch would discard them without a word.
        ...(editor.getHTML() === sent ? { dirty: false } : null),
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
      set({ saving: false, pendingAction: null });
    }
  },

  async saveAsNewVersion(name, options) {
    const { editor, documentId } = get();
    if (!editor || documentId === null) {
      set({ error: "There is no open document to save." });
      return false;
    }
    const isCurrent = captureRequest();
    set({ saving: true, pendingAction: "saveAsNew", error: null });
    try {
      // The live buffer as it was when the request left, for the drift check
      // below. NOT the same as what is sent: ChatPanel passes explicit content.
      const sent = editor.getHTML();
      const created = await createVersion(
        documentId,
        options?.content ?? sent,
        name ?? null,
        options?.source ?? "user",
      );
      // NOTE: a discarded write returns here, so `versionSource` is untouched — the
      // reason a failed or superseded save can never leave "ai" behind.
      if (!isCurrent()) return false;
      // The new version has the highest number and the list is newest-first, so
      // it belongs at the top. Resetting to page 1 is one rule; splicing it into
      // an accumulated list whose offsets have all just shifted by one is
      // several, all wrong somewhere.
      //
      // BEFORE the set() below, not after: App commits a held-back selection the
      // instant `dirty` goes false, and that selection bumps the request token —
      // which would discard this fetch and leave the new version out of the
      // sidebar until a full page reload.
      await get().loadVersions(0);
      if (!isCurrent()) return false;
      set({
        // The patent row shows a version count; without this it keeps saying
        // "1 version" next to a list that now shows two, until the next reload.
        documents: get().documents.map((d) =>
          d.id === documentId
            ? { ...d, version_count: d.version_count + 1, updated_at: created.updated_at }
            : d,
        ),
        // THE DRIFT GUARD — the same rule as `save`, and it matters more here:
        // moving `versionNumber` changes App's remount key, which would rebuild
        // the editor from the server's echo and destroy the keystrokes outright.
        // Drifted, the user stays on their version with their text and `dirty`
        // still set; the created version is in the list, one click away.
        ...(editor.getHTML() === sent
          ? {
              versionNumber: created.version_number,
              versionName: nameOf(created),
              // The Banner's own button defaults to "user"; only ChatPanel passes "ai".
              versionSource: options?.source ?? "user",
              // The server's echo, not `options?.source`: this is the persisted fact,
              // and the two can only ever agree since the server stores exactly what
              // was sent.
              versionOrigin: created.source,
              // The server's sanitised echo is the truth after nh3 ran; moving
              // versionNumber changes the remount key, which rebuilds the editor
              // from exactly this content.
              content: checkedContent(created),
              dirty: false,
            }
          : null),
      });
      return true;
    } catch (error) {
      if (!isCurrent()) return false;
      set({ error: toMessage(error) });
      return false;
    } finally {
      // see `save` — one clear for every exit path
      set({ saving: false, pendingAction: null });
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

  /**
   * One writer: `Editor.onUpdate` sets it; the two save actions clear it when
   * what they saved is still what the editor holds, and the two selection actions
   * clear it when they succeed. Nothing else may clear it — App commits a
   * selection held back by the dirty dialog when this goes false. In particular
   * the list, create and rename actions never touch it.
   */
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
 * would delete every action along with the state.
 */
export function __resetStoreForTests(): void {
  token = 0;
  appending = false;
  useDocumentStore.setState(initialState);
}
