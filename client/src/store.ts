import type { Editor } from "@tiptap/core";
import { create } from "zustand";

// Aliased because each wrapping store action has the same name.
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
} from "./services/api";
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
 * Shared state only: a value lives here because two or more components need it. `editor` is
 * the reason the store exists — ChatPanel must apply AI output to the instance Editor owns,
 * and they are siblings.
 *
 * Deliberately local instead: chat messages, input and attached file (ChatPanel), the pending
 * selection behind the dirty dialog (App), and text in a rename input before it is submitted.
 */
interface DocumentState {
  documents: DocumentSummary[];
  documentsTotal: number;
  documentsOffset: number;
  documentsLimit: number;

  documentId: number | null;
  title: string;

  /** The open patent's versions, newest first. Accumulated, not paged: `versions.length <
   *  versionsTotal` is how the UI knows more remain, so there is no second flag to sync. */
  versions: VersionSummary[];
  versionsTotal: number;
  /** Offset of the most recent fetch; the staleness guard, not a page number. */
  versionsOffset: number;
  versionsLimit: number;

  versionNumber: number | null;
  versionName: string;

  /**
   * Why `versionNumber` last changed. Written in the same set() as `versionNumber`, so it can
   * never disagree with it, and overwritten by every later change, so it cannot get stuck.
   * ChatPanel reads it once with getState() to tell "the user moved" (clear the transcript)
   * from "we moved, because the user accepted an AI edit" (keep it).
   *
   * The one exception to the shared-state rule, justified by atomicity rather than sharing:
   * it describes a store *transition*, and recording it elsewhere records it at a different
   * moment — which is the bug, not the design.
   */
  versionSource: "user" | "ai" | null;

  /** Who created the currently open version, per the server's stored `source`. Ordinary shared
   *  data, kept in step with `versionNumber` like `versionName` is — unlike `versionSource`
   *  above, it survives reload, which is what keeps an AI version pre-consented. */
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
  /** Refused server-side (409) on a document's only version. The UI never lets the user reach
   *  that click, but the store still reports it rather than assuming. */
  deleteVersion(n: number): Promise<boolean>;
  save(): Promise<boolean>;
  /**
   * `name` reaches `createVersion`, so the list reads "AI: delete claim 3", not "Version 4".
   * `options.content` is the exact HTML to save, defaulting to the live buffer, so keystrokes
   * made between an AI apply and this POST are not folded into the AI's version.
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

/** Request ordering, one concept rather than a rule per action. Module scope: nothing renders
 *  it, and it must survive the editor's `key` remount while staying readable from actions. */
let token = 0;

/** "Show more versions" is one button the user can double-click, and both clicks compute the
 *  same offset — so the echoed-offset guard alone cannot tell them apart. */
let appending = false;

/** Selection actions *begin*: they change what the user is looking at. */
function beginRequest(): () => boolean {
  const mine = ++token;
  return () => mine === token;
}

/** Everything else *captures*: a request resolving after the user switched away is discarded.
 *  For a save that means it cannot clear the new editor's dirty flag. */
function captureRequest(): () => boolean {
  const mine = token;
  return () => mine === token;
}

/**
 * `api.ts` casts the response body to its declared type; a proxy or a stale deploy can still
 * hand back another shape. Unchecked, a missing `content` leaves the user staring at a blank
 * pane with no error — a silent failure.
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

/** The whole envelope, not just `items`: a missing `total` renders a pager as "Page 1 of NaN". */
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
 * Appending is not concatenation: a version created between two fetches shifts every row down
 * one, so the next page overlaps what we hold. Merging by `version_number` makes a duplicate
 * impossible, and the sort keeps the list newest-first if a page arrives out of order.
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
    // Optimistic: the pager highlights the clicked page at once, and the offset it holds is
    // what tells a late response it is stale.
    set({ listLoading: true, documentsOffset: offset, error: null });
    try {
      const page = checkedPage(await listDocuments(get().documentsLimit, offset), "patents");
      if (!isCurrent() || get().documentsOffset !== offset) return;
      set({
        documents: page.items,
        documentsTotal: page.total,
        documentsLimit: page.limit,
      });
      // Otherwise the first paint is an empty editor column. Only on the first load — once a
      // patent is open, changing pages must not move it.
      if (page.items.length && get().documentId === null) {
        await get().selectDocument(page.items[0].id);
      }
    } catch (error) {
      // Guarded in the catch too: an aborted load that happens to 404 would otherwise paint
      // "not found" over the document you successfully switched to.
      if (!isCurrent()) return;
      set({ error: toMessage(error) });
    } finally {
      // Every exit path, stale returns included: a stuck `listLoading` disables the pager and
      // "Show more" for the rest of the session.
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
      // Note what is not set: the open version, its content and `dirty`. Loading the list is
      // not a selection — the editor keeps whatever it holds.
      set({
        versions: page.items,
        versionsTotal: page.total,
        versionsLimit: page.limit,
      });
    } catch (error) {
      if (!isCurrent()) return;
      set({ error: toMessage(error) });
    } finally {
      set({ listLoading: false });
    }
  },

  /** "Show more versions": fetches the page after what we hold and appends it. */
  async loadMoreVersions() {
    const { documentId: id, versions, versionsTotal } = get();
    if (id === null || appending || versions.length >= versionsTotal) return;
    const offset = versions.length;
    const isCurrent = captureRequest();
    appending = true;
    set({ listLoading: true, versionsOffset: offset, error: null });
    try {
      const page = checkedPage(await listVersions(id, get().versionsLimit, offset), "versions");
      if (!isCurrent() || get().versionsOffset !== offset) return;
      // `get().versions`, not the destructured copy: whatever is held now is what this page
      // appends to.
      set({
        versions: mergeVersions(get().versions, page.items),
        versionsTotal: page.total,
        versionsLimit: page.limit,
      });
    } catch (error) {
      if (!isCurrent()) return;
      // The list stays exactly as it was; only the error appears.
      set({ error: toMessage(error) });
    } finally {
      appending = false;
      set({ listLoading: false });
    }
  },

  async selectDocument(id) {
    const isCurrent = beginRequest();
    set({ loading: true, error: null });
    try {
      const detail = await getDocument(id);
      if (!isCurrent()) return;
      if (!detail.version_count) {
        // Unreachable against this server, but asking for version 0 would surface as
        // "Version 0 was not found", which reads as a bug.
        set({ loading: false, dirty: false, error: `"${detail.title}" has no saved versions.` });
        return;
      }
      // Read before the Promise.all: a throw inside the array leaves the other request
      // dangling as an unhandled rejection.
      const latest = checkedLatest(detail);
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
      // `dirty` is deliberately not cleared: the editor stays mounted while loading and the
      // remount key never moved, so the user's edited text is still on screen. Clearing the
      // flag would drop the badge, the unload guard and the next dirty dialog over live text.
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
      // See `selectDocument`: a failed switch changes nothing on screen, so `dirty` stays.
      set({ loading: false, error: toMessage(error) });
    }
  },

  /** Creates the patent and opens it — which discards unsaved edits in whatever was open, so
   *  the UI must put this behind the same dirty guard as switching patents. */
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
      // The list is title-ordered, so the new patent may have landed on any page; `total` has
      // to be re-read regardless or the pager under-counts.
      await get().loadDocuments(get().documentsOffset);
      return true;
    } catch (error) {
      if (!isCurrent()) return false;
      // A duplicate title 409s with a readable sentence already. Surface it verbatim.
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
      // The header shows the open patent's title.
      if (get().documentId === updated.id) set({ title: updated.title });
      // A title change reorders the list and can move the row to another page, so refetch
      // rather than patching in place.
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

  /** Version lists are ordered by number, so a rename cannot reorder or re-page them —
   *  patching the row we hold is correct and avoids a refetch flicker. */
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
        // Renaming the open version must change what the bar says. Nothing else moves: PATCH
        // never touches content, so `dirty` and the editor are not this action's business.
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

  /** When the deleted version is the open one, this hands off to `selectDocument`, which picks
   *  the newest survivor the same way a first load picks a version. */
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
        // The patent row shows a version count; without this it keeps the old total.
        documents: get().documents.map((d) =>
          d.id === id ? { ...d, version_count: d.version_count - 1 } : d,
        ),
      });
      // The deleted content is gone, so there is nothing left on screen to call dirty.
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
      // Captured before the request: these are the bytes the server now holds, and the
      // reference point for the drift check below.
      const sent = editor.getHTML();
      // Updates version `n` in place — it must never create a version.
      const saved = await updateVersion(documentId, versionNumber, sent);
      if (!isCurrent()) return false;
      set({
        // The picker shows the last-saved time. Nothing reorders, so there is nothing to
        // refetch.
        versions: get().versions.map((v) =>
          v.version_number === saved.version_number
            ? { ...v, updated_at: saved.updated_at }
            : v,
        ),
        // The drift guard, same rule as ChatPanel's: keystrokes typed while the PUT was in
        // flight are not on the server, and clearing `dirty` over them would let the next
        // version switch discard them without a word.
        ...(editor.getHTML() === sent ? { dirty: false } : null),
      });
      return true;
    } catch (error) {
      if (!isCurrent()) return false;
      set({ error: toMessage(error) });
      return false;
    } finally {
      // Every exit path, including the stale ones: a stuck `saving` disables Save for the
      // rest of the session.
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
      // The live buffer as it was when the request left, for the drift check below. Not
      // necessarily what is sent: ChatPanel passes explicit content.
      const sent = editor.getHTML();
      const created = await createVersion(
        documentId,
        options?.content ?? sent,
        name ?? null,
        options?.source ?? "user",
      );
      // A discarded write returns here, leaving `versionSource` untouched — which is why a
      // failed or superseded save can never leave "ai" behind.
      if (!isCurrent()) return false;
      // Reset to page 1: the new version has the highest number and the list is newest-first.
      // Splicing it into an accumulated list whose offsets just shifted is several rules, all
      // wrong somewhere. Before the set() below, because App commits a held-back selection the
      // instant `dirty` goes false, and that selection would discard this fetch.
      await get().loadVersions(0);
      if (!isCurrent()) return false;
      set({
        documents: get().documents.map((d) =>
          d.id === documentId
            ? { ...d, version_count: d.version_count + 1, updated_at: created.updated_at }
            : d,
        ),
        // The drift guard again, and it matters more here: moving `versionNumber` changes
        // App's remount key, which would rebuild the editor from the server's echo and destroy
        // the keystrokes. Drifted, the user keeps their text and the version is one click away.
        ...(editor.getHTML() === sent
          ? {
              versionNumber: created.version_number,
              versionName: nameOf(created),
              // The Banner's button defaults to "user"; only ChatPanel passes "ai".
              versionSource: options?.source ?? "user",
              // The server's echo, not `options?.source`: this is the persisted fact.
              versionOrigin: created.source,
              // The sanitised echo is the truth after nh3 ran, and the remount rebuilds the
              // editor from exactly this content.
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
      set({ saving: false, pendingAction: null });
    }
  },

  setEditor(editor) {
    set({ editor });
  },

  /** Identity-guarded: during a key change React can commit the new child's `onCreate` before
   *  the old child's `onDestroy`, and an unguarded clear would null the live editor. */
  clearEditor(editor) {
    set((s) => (s.editor === editor ? { editor: null } : {}));
  },

  /** One writer: `Editor.onUpdate` sets it; the two save actions clear it when what they saved
   *  is still what the editor holds, and the two selection actions clear it on success.
   *  Nothing else may — App commits a held-back selection when this goes false. */
  setDirty(dirty) {
    set({ dirty });
  },

  clearError() {
    set({ error: null });
  },
}));

/** Zustand stores are module singletons; without this they leak between tests. `setState`
 *  merges — the `(initial, true)` replace form would delete every action too. */
export function __resetStoreForTests(): void {
  token = 0;
  appending = false;
  useDocumentStore.setState(initialState);
}
