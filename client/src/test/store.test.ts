import type { Editor } from "@tiptap/core";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  DocumentDetail,
  DocumentPage,
  VersionPage,
  VersionRead,
  VersionSummary,
} from "../types";

// The store's only dependency is the api module, so one `vi.mock` isolates it
// completely. `toMessage` and `ApiError` are kept real — they are pure, and the
// error text the store surfaces should be the text the app really shows.
vi.mock("../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api")>()),
  listDocuments: vi.fn(),
  createDocument: vi.fn(),
  getDocument: vi.fn(),
  renameDocument: vi.fn(),
  listVersions: vi.fn(),
  getVersion: vi.fn(),
  createVersion: vi.fn(),
  updateVersion: vi.fn(),
  renameVersion: vi.fn(),
  deleteVersion: vi.fn(),
}));

const {
  ApiError,
  createDocument,
  createVersion,
  deleteVersion: apiDeleteVersion,
  getDocument,
  getVersion,
  listDocuments,
  listVersions,
  renameDocument,
  renameVersion,
  updateVersion,
} = vi.mocked(await import("../api"));
const { useDocumentStore, PAGE_SIZE } = await import("../store");

const store = () => useDocumentStore.getState();

const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

const STAMP = "2026-01-01T00:00:00";

function detail(id: number, versionCount = 1, latest = versionCount): DocumentDetail {
  return {
    id,
    title: `Patent ${id}`,
    version_count: versionCount,
    latest_version_number: latest,
    created_at: STAMP,
    updated_at: STAMP,
  };
}

function summary(n: number, updated = `2026-01-0${n}T00:00:00`): VersionSummary {
  return { version_number: n, name: `Version ${n}`, created_at: STAMP, updated_at: updated };
}

/** Versions come back newest first, which is the order the store must preserve. */
function versionPage(numbers: number[], extra: Partial<VersionPage> = {}): VersionPage {
  const items = [...numbers].sort((a, b) => b - a).map((n) => summary(n));
  return { items, total: items.length, limit: PAGE_SIZE, offset: 0, ...extra };
}

function documentPage(ids: number[], extra: Partial<DocumentPage> = {}): DocumentPage {
  return {
    items: ids.map((id) => ({ id, title: `Patent ${id}`, version_count: 1, updated_at: STAMP })),
    total: ids.length,
    limit: PAGE_SIZE,
    offset: 0,
    ...extra,
  };
}

function version(id: number, n: number, content = `<p>doc ${id} v${n}</p>`): VersionRead {
  return {
    document_id: id,
    version_number: n,
    name: `Version ${n}`,
    content,
    created_at: STAMP,
    updated_at: `2026-01-0${n}T00:00:00`,
  };
}

/**
 * The store only ever calls `getHTML()` on the editor, so a two-field fake is
 * the whole contract. Mounting TipTap in jsdom to assert this would test jsdom.
 */
function fakeEditor(html = "<p>live</p>"): Editor {
  return { getHTML: () => html } as unknown as Editor;
}

/**
 * The same two-field contract, but reading a value the test can change *during*
 * a request — which is the only way to write the drift guards, because they
 * compare `getHTML()` before the await with `getHTML()` after it.
 */
function mutableEditor(read: () => string): Editor {
  return { getHTML: read } as unknown as Editor;
}

// The store itself is reset in `test/setup.ts`'s afterEach.
beforeEach(() => {
  for (const fn of [
    listDocuments,
    createDocument,
    getDocument,
    renameDocument,
    listVersions,
    getVersion,
    createVersion,
    updateVersion,
    renameVersion,
    apiDeleteVersion,
  ]) {
    fn.mockReset();
  }
  // Every `selectDocument` also fetches page 1 of the version list; the tests
  // that care override it.
  listVersions.mockResolvedValue(versionPage([1]));
});

describe("selection ordering", () => {
  // S1 — the test the whole token mechanism exists for.
  it("a stale document load is discarded", async () => {
    getDocument.mockImplementation(async (id: number) => {
      if (id === 1) await delay(50);
      return detail(id);
    });
    getVersion.mockImplementation(async (id: number, n: number) => {
      if (id === 1) await delay(50);
      return version(id, n);
    });

    const slow = store().selectDocument(1);
    const fast = store().selectDocument(2);
    await Promise.all([fast, slow]);

    expect(store().documentId).toBe(2);
    expect(store().title).toBe("Patent 2");
    expect(store().content).toBe("<p>doc 2 v1</p>");
    expect(store().loading).toBe(false);
  });

  // S1, second shape: the switch happens *after* the detail arrives, so the
  // guard that has to catch it is the one after the content request. This is the
  // more likely shape live — the detail is small and the content is large.
  it("a document load is discarded when the switch happens mid-load", async () => {
    getDocument.mockImplementation(async (id: number) => detail(id));
    getVersion.mockImplementation(async (id: number, n: number) => {
      if (id === 1) await delay(50);
      return version(id, n);
    });

    const slow = store().selectDocument(1);
    await delay(0); // doc 1 is now past its first guard, awaiting its content
    const fast = store().selectDocument(2);
    await Promise.all([fast, slow]);

    expect(store().documentId).toBe(2);
    expect(store().content).toBe("<p>doc 2 v1</p>");
  });

  // S2 — the guard that regresses: the `catch` path needs the same check.
  it("a stale failure does not paint an error", async () => {
    getDocument.mockImplementation(async (id: number) => {
      if (id === 1) {
        await delay(50);
        throw new Error("Document 1 not found.");
      }
      return detail(id);
    });
    getVersion.mockImplementation(async (id: number, n: number) => version(id, n));

    const doomed = store().selectDocument(1);
    const wanted = store().selectDocument(2);
    await Promise.all([wanted, doomed]);

    expect(store().error).toBeNull();
    expect(store().documentId).toBe(2);
    expect(store().content).toBe("<p>doc 2 v1</p>");
  });

  it("selectDocument opens the newest version the server names", async () => {
    getDocument.mockResolvedValue(detail(4, 3, 3));
    listVersions.mockResolvedValue(versionPage([1, 2, 3]));
    getVersion.mockResolvedValue(version(4, 3));

    await store().selectDocument(4);

    // `latest_version_number`, not "the largest number on page 1" — with 40
    // versions the newest may not even be on the page we hold.
    expect(getVersion).toHaveBeenCalledWith(4, 3);
    expect(store().versionNumber).toBe(3);
    expect(store().versionName).toBe("Version 3");
    expect(store().versions.map((v) => v.version_number)).toEqual([3, 2, 1]);
    expect(store().versionsTotal).toBe(3);
    expect(store().versionsOffset).toBe(0);
  });

  // S8 — Task 1 requirement 2's only client-side proof.
  it("selectVersion loads that version's content and name and clears dirty", async () => {
    getVersion.mockResolvedValue({ ...version(1, 1, "<p>older draft</p>"), name: "First filing" });
    useDocumentStore.setState({ documentId: 1, versionNumber: 3, dirty: true });

    await store().selectVersion(1);

    expect(getVersion).toHaveBeenCalledWith(1, 1);
    expect(store().versionNumber).toBe(1);
    expect(store().versionName).toBe("First filing");
    expect(store().content).toBe("<p>older draft</p>");
    expect(store().dirty).toBe(false);
  });

  // S8, staleness half: the version picker is the easiest control in the app to
  // click twice quickly.
  it("a stale version load is discarded", async () => {
    getVersion.mockImplementation(async (id: number, n: number) => {
      if (n === 1) await delay(50);
      return version(id, n);
    });
    useDocumentStore.setState({ documentId: 1, versionNumber: 3 });

    const slow = store().selectVersion(1);
    const fast = store().selectVersion(2);
    await Promise.all([fast, slow]);

    expect(store().versionNumber).toBe(2);
    expect(store().content).toBe("<p>doc 1 v2</p>");
  });

  // The failure path of both selection actions. App keeps the editor MOUNTED
  // while loading — it only dims it — and a failed switch leaves documentId and
  // versionNumber where they were, so the remount key never changes and the
  // user's edited text is still on screen. Clearing `dirty` over it removed the
  // badge, the beforeunload guard and the dialog on the next switch, and the next
  // switch then discarded the edits in silence.
  // Repro: backend down -> type -> switch version -> the text is still there.
  it("a failed switch leaves dirty set, because the edits are still on screen", async () => {
    getVersion.mockRejectedValue(new Error("Cannot reach the server."));
    useDocumentStore.setState({ documentId: 1, versionNumber: 1, dirty: true });

    await store().selectVersion(2);

    expect(store().dirty).toBe(true);
    expect(store().versionNumber).toBe(1); // nothing moved, so nothing remounted
    expect(store().error).toBe("Cannot reach the server.");
    expect(store().loading).toBe(false);

    // Same reasoning, other selection action.
    getDocument.mockRejectedValue(new Error("Cannot reach the server."));
    useDocumentStore.setState({ dirty: true });

    await store().selectDocument(2);

    expect(store().dirty).toBe(true);
    expect(store().documentId).toBe(1);
    expect(store().error).toBe("Cannot reach the server.");
  });

  // `api.ts` casts the body to its declared type, so a proxy, a stale deploy or a
  // 200 from the wrong route reaches the store as the wrong shape. Unguarded, a
  // missing `content` sets `content: null`: no editor, no error, no loading state
  // — a blank pane and nothing to read.
  it("a malformed version or document body becomes a readable error", async () => {
    getVersion.mockResolvedValue({ ...version(1, 1), content: undefined } as unknown as VersionRead);
    useDocumentStore.setState({ documentId: 1, versionNumber: 1 });

    await store().selectVersion(2);

    expect(store().error).toBe("The server returned a version without any content.");
    expect(store().content).toBeNull();
    expect(store().loading).toBe(false);

    getDocument.mockResolvedValue({
      id: 2,
      title: "Patent 2",
      version_count: 2,
    } as unknown as DocumentDetail);

    await store().selectDocument(2);

    expect(store().error).toBe("The server returned a document without a latest version number.");
    expect(store().loading).toBe(false);
  });

  it("reports a document with no versions instead of asking for version 0", async () => {
    getDocument.mockResolvedValue({ ...detail(9, 0, 0), title: "Empty patent" });

    await store().selectDocument(9);

    expect(getVersion).not.toHaveBeenCalled();
    expect(store().error).toBe('"Empty patent" has no saved versions.');
    expect(store().loading).toBe(false);
  });
});

describe("pagination", () => {
  // Not in the §11 table: `loadDocuments`' auto-select is the reason the first
  // paint is not an empty editor column, and nothing else covers it.
  it("loadDocuments stores the page and selects the first document", async () => {
    listDocuments.mockResolvedValue(documentPage([4, 5], { total: 42 }));
    getDocument.mockResolvedValue(detail(4, 2, 2));
    listVersions.mockResolvedValue(versionPage([1, 2]));
    getVersion.mockResolvedValue(version(4, 2));

    await store().loadDocuments();

    expect(listDocuments).toHaveBeenCalledWith(PAGE_SIZE, 0);
    expect(store().documents).toHaveLength(2);
    expect(store().documentsTotal).toBe(42);
    expect(store().documentId).toBe(4);
    expect(store().versionNumber).toBe(2);
    expect(store().listLoading).toBe(false);
  });

  // Paging the patent list is not a selection: it must not re-open anything, and
  // it must not disturb the document being edited.
  it("changing the patent page keeps the open document and its unsaved edits", async () => {
    listDocuments.mockResolvedValue(documentPage([9, 10], { total: 42, offset: 20 }));
    useDocumentStore.setState({ documentId: 4, content: "<p>edited</p>", dirty: true });

    await store().loadDocuments(20);

    expect(listDocuments).toHaveBeenCalledWith(PAGE_SIZE, 20);
    expect(store().documentsOffset).toBe(20);
    expect(store().documents.map((d) => d.id)).toEqual([9, 10]);
    expect(getDocument).not.toHaveBeenCalled(); // no auto-select once one is open
    expect(store().documentId).toBe(4);
    expect(store().dirty).toBe(true);
    expect(store().content).toBe("<p>edited</p>");
  });

  // The pager is a row of buttons and clicking two quickly is one flick of the
  // wrist; the echoed `offset` is what tells the first response it lost.
  it("a page response for an offset the user has left is discarded", async () => {
    listDocuments.mockImplementation(async (_limit: number, offset: number) => {
      if (offset === 20) await delay(50);
      return documentPage(offset === 20 ? [9] : [11], { total: 42, offset });
    });
    // A patent is already open, so nothing here bumps the request token: the
    // echoed offset is the only thing that can tell these two responses apart.
    useDocumentStore.setState({ documentId: 3 });

    const slow = store().loadDocuments(20);
    const fast = store().loadDocuments(40);
    await Promise.all([fast, slow]);

    expect(store().documentsOffset).toBe(40);
    expect(store().documents.map((d) => d.id)).toEqual([11]);
  });

  // Same reasoning as the version/document guards: an envelope without `items`
  // or `total` would render an empty list and a "Page 1 of NaN" pager, with
  // nothing anywhere saying the request went wrong.
  it("a malformed page envelope becomes a readable error", async () => {
    listDocuments.mockResolvedValue({ items: [{ id: 1 }] } as unknown as DocumentPage);

    await store().loadDocuments();

    expect(store().error).toBe("The server returned an unreadable list of patents.");
    expect(store().listLoading).toBe(false);
  });
});

// The version tree shows page 1 and a "Show more versions" control that appends;
// there is no pager, so `versions` accumulates and `versionsTotal` is how the UI
// knows whether the control still has anything to fetch.
describe("appending versions", () => {
  it("loadMoreVersions appends the next page without touching the editor", async () => {
    listVersions.mockResolvedValue(versionPage([1, 2], { total: 4, offset: 2 }));
    useDocumentStore.setState({
      documentId: 1,
      versions: [summary(4), summary(3)],
      versionsTotal: 4,
      versionNumber: 4,
      content: "<p>edited</p>",
      dirty: true,
    });

    await store().loadMoreVersions();

    // The offset is what we already hold, not a page index.
    expect(listVersions).toHaveBeenCalledWith(1, PAGE_SIZE, 2);
    expect(store().versions.map((v) => v.version_number)).toEqual([4, 3, 2, 1]);
    expect(store().versionsTotal).toBe(4);
    expect(store().listLoading).toBe(false);
    // Appending is not a selection: the open version and its unsaved edits stay.
    expect(store().versionNumber).toBe(4);
    expect(store().content).toBe("<p>edited</p>");
    expect(store().dirty).toBe(true);
  });

  // "Show more" is one button, and both clicks compute the same offset — so the
  // echoed-offset guard cannot separate them and the in-flight flag must.
  it("two fast clicks fetch once and duplicate nothing", async () => {
    listVersions.mockImplementation(async () => {
      await delay(20);
      return versionPage([1, 2], { total: 4, offset: 2 });
    });
    useDocumentStore.setState({
      documentId: 1,
      versions: [summary(4), summary(3)],
      versionsTotal: 4,
    });

    await Promise.all([store().loadMoreVersions(), store().loadMoreVersions()]);

    expect(listVersions).toHaveBeenCalledTimes(1);
    expect(store().versions.map((v) => v.version_number)).toEqual([4, 3, 2, 1]);
  });

  // The other way a duplicate gets in: a version created between the two fetches
  // shifts every row down one, so offset 2 returns a row we already hold.
  it("a page overlapping the held list does not duplicate rows", async () => {
    listVersions.mockResolvedValue(versionPage([2, 3], { total: 5, offset: 2 }));
    useDocumentStore.setState({
      documentId: 1,
      versions: [summary(4), summary(3)],
      versionsTotal: 5,
    });

    await store().loadMoreVersions();

    expect(store().versions.map((v) => v.version_number)).toEqual([4, 3, 2]);
  });

  it("loadMoreVersions is a no-op with everything loaded, and with nothing open", async () => {
    useDocumentStore.setState({
      documentId: 1,
      versions: [summary(2), summary(1)],
      versionsTotal: 2,
    });

    await store().loadMoreVersions();

    expect(listVersions).not.toHaveBeenCalled();

    useDocumentStore.setState({ documentId: null, versions: [], versionsTotal: 5 });

    await store().loadMoreVersions();

    expect(listVersions).not.toHaveBeenCalled();
  });

  // `selectDocument` and `saveAsNewVersion` both call this, and both mean
  // "throw away what is expanded and start again from the newest page".
  it("loadVersions(0) resets the accumulated list to the first page", async () => {
    listVersions.mockResolvedValue(versionPage([3, 4], { total: 4 }));
    useDocumentStore.setState({
      documentId: 1,
      versions: [summary(4), summary(3), summary(2), summary(1)],
      versionsTotal: 4,
      versionsOffset: 2,
      versionNumber: 4,
      content: "<p>edited</p>",
      dirty: true,
    });

    await store().loadVersions(0);

    expect(store().versions.map((v) => v.version_number)).toEqual([4, 3]);
    expect(store().versionsOffset).toBe(0);
    // A reset is still not a selection.
    expect(store().content).toBe("<p>edited</p>");
    expect(store().dirty).toBe(true);
  });

  // S11 — every early return used to skip the flag, and nothing later clears it:
  // one click on a patent row during a list fetch left the pager and "Show more
  // versions" disabled for the rest of the session.
  it("listLoading clears even when the list response is discarded as stale", async () => {
    listVersions.mockImplementation(async () => {
      await delay(30);
      return versionPage([1]);
    });
    getDocument.mockResolvedValue(detail(2));
    getVersion.mockResolvedValue(version(2, 1));
    useDocumentStore.setState({ documentId: 1 });

    const listing = store().loadVersions(0);
    await store().selectDocument(2); // bumps the token: `listing` is now stale
    await listing;

    expect(store().listLoading).toBe(false);
  });

  it("a malformed page while appending explains itself and keeps the list", async () => {
    listVersions.mockResolvedValue(null as unknown as VersionPage);
    useDocumentStore.setState({
      documentId: 1,
      versions: [summary(4), summary(3)],
      versionsTotal: 4,
    });

    await store().loadMoreVersions();

    expect(store().error).toBe("The server returned an unreadable list of versions.");
    expect(store().versions.map((v) => v.version_number)).toEqual([4, 3]);
    expect(store().listLoading).toBe(false);
    // The flag has to clear or "Show more" is dead for the rest of the session.
    listVersions.mockResolvedValue(versionPage([1, 2], { total: 4, offset: 2 }));

    await store().loadMoreVersions();

    expect(store().versions.map((v) => v.version_number)).toEqual([4, 3, 2, 1]);
  });
});

describe("saving", () => {
  beforeEach(() => {
    useDocumentStore.setState({
      editor: fakeEditor(),
      documentId: 1,
      versionNumber: 2,
      versionName: "Version 2",
      // Two versions, newest first, so "refresh the saved one" is
      // distinguishable from "refresh them all".
      versions: [summary(2), summary(1)],
      versionsTotal: 2,
      content: "<p>doc 1 v2</p>",
      dirty: true,
    });
  });

  // S3 — with a mocked client, "creates no version" means exactly this.
  it("save() PUTs the live editor HTML and does not call createVersion", async () => {
    updateVersion.mockResolvedValue({
      ...version(1, 2, "<p>live</p>"),
      updated_at: "2026-03-03T00:00:00",
    });

    await expect(store().save()).resolves.toBe(true);

    expect(updateVersion).toHaveBeenCalledTimes(1);
    expect(updateVersion).toHaveBeenCalledWith(1, 2, "<p>live</p>");
    expect(createVersion).not.toHaveBeenCalled();
    expect(store().versions).toHaveLength(2);
    // Only the saved version's "saved at" is refreshed, and it is refreshed —
    // otherwise the picker shows a stale time forever.
    expect(store().versions).toEqual([
      { ...summary(2), updated_at: "2026-03-03T00:00:00" },
      summary(1),
    ]);
    expect(store().versionNumber).toBe(2);
    expect(store().dirty).toBe(false);
    expect(store().saving).toBe(false);
  });

  // S4
  it("saveAsNewVersion() selects the created version and jumps the picker to page 1", async () => {
    createVersion.mockResolvedValue({
      ...version(1, 3, "<p>sanitised by nh3</p>"),
      name: "Version 3",
    });
    listVersions.mockResolvedValue(versionPage([1, 2, 3], { total: 3 }));
    useDocumentStore.setState({ versionsOffset: 20 }); // the user had paged away

    await expect(store().saveAsNewVersion()).resolves.toBe(true);

    // The third argument is the version name, which only ChatPanel ever supplies;
    // `null` is what `createVersion` defaulted to before it was threaded through.
    expect(createVersion).toHaveBeenCalledWith(1, "<p>live</p>", null);
    expect(store().versionNumber).toBe(3);
    expect(store().versionName).toBe("Version 3");
    // From the 201 body: the server's sanitised echo, not the editor's HTML.
    expect(store().content).toBe("<p>sanitised by nh3</p>");
    expect(store().dirty).toBe(false);
    // The new version is the newest, so it is on page 1 — which is where the
    // picker now is, refetched rather than spliced.
    expect(listVersions).toHaveBeenCalledWith(1, PAGE_SIZE, 0);
    expect(store().versionsOffset).toBe(0);
    expect(store().versions.map((v) => v.version_number)).toEqual([3, 2, 1]);
    expect(store().versionsTotal).toBe(3);
  });

  // S4b — the three things ChatPanel adds, and only ChatPanel uses. `content` is the
  // load-bearing one: between the AI's setContent and this POST the user can type,
  // and folding those keystrokes into the version would put text in it that the AI
  // never produced and the user never reviewed as part of that change.
  it("saveAsNewVersion() honours an explicit name, content and source", async () => {
    createVersion.mockResolvedValue({ ...version(1, 3, "<p>ai</p>"), name: "AI: delete claim 3" });
    listVersions.mockResolvedValue(versionPage([1, 2, 3], { total: 3 }));

    await expect(
      store().saveAsNewVersion("AI: delete claim 3", { source: "ai", content: "<p>ai</p>" }),
    ).resolves.toBe(true);

    // NOT "<p>live</p>", which is what the editor holds.
    expect(createVersion).toHaveBeenCalledWith(1, "<p>ai</p>", "AI: delete claim 3");
    expect(store().versionSource).toBe("ai");
    expect(store().versionNumber).toBe(3);
  });

  // S5 — capture-vs-bump. A save that lands after a switch must not clear the
  // dirty flag of the document the user is now editing.
  it("a save resolving after a switch does not clear the new dirty flag", async () => {
    updateVersion.mockImplementation(async () => {
      await delay(50);
      return version(1, 1);
    });
    getDocument.mockResolvedValue(detail(2));
    getVersion.mockResolvedValue(version(2, 1));

    const saving = store().save();
    await store().selectDocument(2); // bumps the token
    store().setDirty(true); // the user types in the new document

    await expect(saving).resolves.toBe(false);
    expect(store().dirty).toBe(true);
    expect(store().documentId).toBe(2);
    expect(store().content).toBe("<p>doc 2 v1</p>");
    // Discarded, but the flag must still clear: nothing else ever will, and a
    // stuck `saving` disables the Save buttons for the rest of the session.
    expect(store().saving).toBe(false);
  });

  // S5, the other half of capture-vs-bump: a save must *not* bump the token, or
  // it would discard the document switch the user started before it.
  it("a save started during a selection does not discard that selection", async () => {
    getDocument.mockImplementation(async (id: number) => {
      await delay(50);
      return detail(id);
    });
    getVersion.mockResolvedValue(version(2, 1));
    updateVersion.mockResolvedValue(version(1, 1));

    const selecting = store().selectDocument(2);
    const saving = store().save();
    await Promise.all([saving, selecting]);

    expect(store().documentId).toBe(2);
    expect(store().content).toBe("<p>doc 2 v1</p>");
  });

  // S5, applied to the other save action: it has more to get wrong — it must
  // not touch the version list, must not move `versionNumber`, and must not
  // clear the new document's dirty flag.
  it("a saveAsNewVersion resolving after a switch is discarded entirely", async () => {
    createVersion.mockImplementation(async () => {
      await delay(50);
      return version(1, 3, "<p>late</p>");
    });
    getDocument.mockResolvedValue(detail(2));
    getVersion.mockResolvedValue(version(2, 1));
    listVersions.mockResolvedValue(versionPage([1]));

    const saving = store().saveAsNewVersion();
    await store().selectDocument(2);
    store().setDirty(true);

    await expect(saving).resolves.toBe(false);
    expect(store().documentId).toBe(2);
    expect(store().versions).toEqual(versionPage([1]).items); // nothing appended
    expect(store().versionNumber).toBe(1);
    expect(store().content).toBe("<p>doc 2 v1</p>");
    expect(store().dirty).toBe(true);
    expect(store().saving).toBe(false);
  });

  // S9 — the confirmed data-loss repro: throttle the network, type, Save, keep
  // typing. Those keystrokes are NOT in the PUT, so clearing `dirty` over them
  // drops the badge, the beforeunload guard and the dirty dialog, and the next
  // version switch discards them without a word.
  it("a save does not clear dirty for keystrokes typed while it was in flight", async () => {
    let html = "<p>typed</p>";
    useDocumentStore.setState({ editor: mutableEditor(() => html) });
    updateVersion.mockImplementation(async () => {
      await delay(20);
      return { ...version(1, 2), updated_at: "2026-03-03T00:00:00" };
    });

    const saving = store().save();
    html = "<p>typed DURING-SAVE</p>"; // the user keeps typing

    await expect(saving).resolves.toBe(true);

    expect(updateVersion).toHaveBeenCalledWith(1, 2, "<p>typed</p>"); // what the server got
    expect(store().dirty).toBe(true); // …which is not what the editor holds
    expect(store().saving).toBe(false);
  });

  // The same rule on the other save, where getting it wrong is worse: moving
  // `versionNumber` changes App's remount key, so the keystrokes are not merely
  // marked saved, they are destroyed.
  it("saveAsNewVersion keeps keystrokes typed during it, and does not remount", async () => {
    let html = "<p>typed</p>";
    useDocumentStore.setState({ editor: mutableEditor(() => html) });
    createVersion.mockImplementation(async () => {
      await delay(20);
      return version(1, 3, "<p>sanitised by nh3</p>");
    });
    listVersions.mockResolvedValue(versionPage([1, 2, 3], { total: 3 }));

    const saving = store().saveAsNewVersion();
    html = "<p>typed DURING-SAVE</p>";

    await expect(saving).resolves.toBe(true);

    expect(createVersion).toHaveBeenCalledWith(1, "<p>typed</p>", null);
    // The version exists and is in the list — but the user stays where they are,
    // with their text and their dirty flag.
    expect(store().versions.map((v) => v.version_number)).toEqual([3, 2, 1]);
    expect(store().versionNumber).toBe(2);
    expect(store().content).toBe("<p>doc 1 v2</p>");
    expect(store().dirty).toBe(true);
  });

  // S10 — the stale-sidebar repro: with unsaved edits, click another version and
  // answer the dialog with "Save as new version". App commits the held-back switch
  // the instant `dirty` goes false, and that switch bumps the request token — so
  // the version list must already be refreshed by then, or its response is
  // discarded and the new version is invisible until a full reload.
  it("the created version is in the list before dirty clears", async () => {
    createVersion.mockResolvedValue(version(1, 3));
    listVersions.mockResolvedValue(versionPage([1, 2, 3], { total: 3 }));

    let versionsWhenClean: number[] | null = null;
    const unsubscribe = useDocumentStore.subscribe((state) => {
      if (!state.dirty && versionsWhenClean === null) {
        versionsWhenClean = state.versions.map((v) => v.version_number);
      }
    });

    await expect(store().saveAsNewVersion()).resolves.toBe(true);
    unsubscribe();

    expect(versionsWhenClean).toEqual([3, 2, 1]);
  });

  it("reports a failed save instead of clearing the dirty flag", async () => {
    updateVersion.mockRejectedValue(new Error("Version 2 of document 1 was not found."));

    await expect(store().save()).resolves.toBe(false);

    expect(store().error).toBe("Version 2 of document 1 was not found.");
    expect(store().dirty).toBe(true);
    expect(store().saving).toBe(false);
    expect(store().pendingAction).toBeNull();
  });

  // `pendingAction` is what makes only the pressed button spin. `saving` alone
  // cannot do it — it is true for all of them.
  it("pendingAction names the write in flight and clears when it resolves", async () => {
    let finish: ((v: VersionRead) => void) | undefined;
    updateVersion.mockImplementation(
      () =>
        new Promise<VersionRead>((resolve) => {
          finish = resolve;
        }),
    );

    const saving = store().save();
    expect(store().pendingAction).toBe("save");
    expect(store().saving).toBe(true);

    finish!(version(1, 2));
    await saving;

    expect(store().pendingAction).toBeNull();
    expect(store().saving).toBe(false);
  });

  // The other write, and the exit path that is easy to forget: a stuck spinner on
  // a failed create would sit there for the rest of the session.
  it("pendingAction distinguishes saveAsNew and clears when it fails", async () => {
    createVersion.mockRejectedValue(new Error("Could not create a version."));

    const saving = store().saveAsNewVersion();
    expect(store().pendingAction).toBe("saveAsNew");

    await expect(saving).resolves.toBe(false);

    expect(store().pendingAction).toBeNull();
    expect(store().saving).toBe(false);
    expect(store().error).toBe("Could not create a version.");
    expect(store().dirty).toBe(true);
  });
});

describe("saving with nothing open", () => {
  // Both writes early-return. The UI disables the buttons, but the store is the
  // thing the AI panel and any future caller will reach through — it has to
  // answer with a sentence rather than a silent `false`.
  it("both save actions refuse, explain, and call no api helper", async () => {
    await expect(store().save()).resolves.toBe(false);
    expect(store().error).toBe("There is no open document to save.");

    useDocumentStore.setState({ error: null });

    await expect(store().saveAsNewVersion()).resolves.toBe(false);
    expect(store().error).toBe("There is no open document to save.");

    expect(updateVersion).not.toHaveBeenCalled();
    expect(createVersion).not.toHaveBeenCalled();
  });
});

describe("creating and renaming", () => {
  it("createDocument opens the new patent and refreshes the visible page", async () => {
    createDocument.mockResolvedValue(detail(7));
    getDocument.mockResolvedValue(detail(7));
    getVersion.mockResolvedValue(version(7, 1, "<p>fresh</p>"));
    listDocuments.mockResolvedValue(documentPage([7, 9], { total: 21, offset: 20 }));
    useDocumentStore.setState({ documentId: 3, documentsOffset: 20 });

    await expect(store().createDocument("  Widget  ")).resolves.toBe(true);

    // Trimmed before it leaves the client, so the list cannot show " Widget".
    expect(createDocument).toHaveBeenCalledWith("Widget", null);
    expect(store().documentId).toBe(7);
    expect(store().content).toBe("<p>fresh</p>");
    expect(store().versionNumber).toBe(1);
    // The page the user was on is refetched: the title ordering means the new
    // patent could be anywhere, and `total` has changed either way.
    expect(listDocuments).toHaveBeenCalledWith(PAGE_SIZE, 20);
    expect(store().documentsTotal).toBe(21);
    expect(store().pendingAction).toBeNull();
    expect(store().saving).toBe(false);
  });

  // The naming rules exist so the server can write a sentence worth reading;
  // the client's job is to not replace it with "Request failed (409)".
  it("a duplicate title shows the server's sentence and changes nothing", async () => {
    createDocument.mockRejectedValue(new ApiError(409, 'A patent called "Widget" already exists.'));
    useDocumentStore.setState({ documentId: 3, title: "Patent 3", content: "<p>open</p>" });

    await expect(store().createDocument("Widget")).resolves.toBe(false);

    expect(store().error).toBe('A patent called "Widget" already exists.');
    expect(store().documentId).toBe(3);
    expect(store().content).toBe("<p>open</p>");
    expect(getDocument).not.toHaveBeenCalled();
    expect(store().pendingAction).toBeNull();
  });

  it("renameDocument retitles the open patent and reloads the list page", async () => {
    renameDocument.mockResolvedValue({ ...detail(3), title: "Widget II" });
    listDocuments.mockResolvedValue(documentPage([3], { total: 1 }));
    useDocumentStore.setState({ documentId: 3, title: "Patent 3" });

    await expect(store().renameDocument(3, " Widget II ")).resolves.toBe(true);

    expect(renameDocument).toHaveBeenCalledWith(3, "Widget II");
    expect(store().title).toBe("Widget II");
    // Titles order the list, so the row may have moved pages — refetch, do not
    // patch in place.
    expect(listDocuments).toHaveBeenCalledTimes(1);
  });

  it("renaming the open version updates the picker and the bar, and only that", async () => {
    renameVersion.mockResolvedValue({ ...version(1, 2), name: "Filed draft" });
    useDocumentStore.setState({
      documentId: 1,
      versionNumber: 2,
      versionName: "Version 2",
      versions: [summary(2), summary(1)],
      content: "<p>doc 1 v2</p>",
      dirty: true,
    });

    await expect(store().renameVersion(2, "  Filed draft  ")).resolves.toBe(true);

    expect(renameVersion).toHaveBeenCalledWith(1, 2, "Filed draft");
    expect(store().versionName).toBe("Filed draft");
    expect(store().versions).toEqual([{ ...summary(2), name: "Filed draft" }, summary(1)]);
    // A rename is not a save and not a selection: the draft in the editor and
    // the dirty flag are none of its business.
    expect(store().content).toBe("<p>doc 1 v2</p>");
    expect(store().dirty).toBe(true);
    expect(listVersions).not.toHaveBeenCalled(); // numbers order the list; nothing moved
  });

  it("renaming another version leaves the open version's name alone", async () => {
    renameVersion.mockResolvedValue({ ...version(1, 1), name: "Superseded" });
    useDocumentStore.setState({
      documentId: 1,
      versionNumber: 2,
      versionName: "Version 2",
      versions: [summary(2), summary(1)],
    });

    await expect(store().renameVersion(1, "Superseded")).resolves.toBe(true);

    expect(store().versionName).toBe("Version 2");
    expect(store().versions[1].name).toBe("Superseded");
  });

  // Whitespace-only is a 422 on the server; catching it here costs a round trip
  // and gives a better sentence than "String should have at least 1 character".
  it("refuses a blank name without calling the server", async () => {
    await expect(store().createDocument("   ")).resolves.toBe(false);
    expect(store().error).toBe("A patent needs a title.");

    useDocumentStore.setState({ documentId: 1 });
    await expect(store().renameVersion(1, "  ")).resolves.toBe(false);
    expect(store().error).toBe("A version needs a name.");

    expect(createDocument).not.toHaveBeenCalled();
    expect(renameVersion).not.toHaveBeenCalled();
  });

  // Same capture-vs-bump rule as the saves: a rename that lands after the user
  // has opened another patent must not retitle the one now on screen.
  it("a rename resolving after a switch is discarded", async () => {
    renameDocument.mockImplementation(async () => {
      await delay(50);
      return { ...detail(1), title: "Widget II" };
    });
    getDocument.mockResolvedValue(detail(2));
    getVersion.mockResolvedValue(version(2, 1));
    useDocumentStore.setState({ documentId: 1, title: "Patent 1" });

    const renaming = store().renameDocument(1, "Widget II");
    await store().selectDocument(2);

    await expect(renaming).resolves.toBe(false);
    expect(store().title).toBe("Patent 2");
    expect(store().saving).toBe(false);
  });
});

describe("deleting a version", () => {
  it("deletes another version and only patches the counts, leaving the open one alone", async () => {
    apiDeleteVersion.mockResolvedValue(undefined);
    useDocumentStore.setState({
      documentId: 1,
      versionNumber: 2,
      versionName: "Version 2",
      versions: [summary(2), summary(1)],
      versionsTotal: 2,
      content: "<p>doc 1 v2</p>",
      dirty: true,
      documents: [{ id: 1, title: "Patent 1", version_count: 2, updated_at: STAMP }],
    });

    await expect(store().deleteVersion(1)).resolves.toBe(true);

    expect(apiDeleteVersion).toHaveBeenCalledWith(1, 1);
    expect(store().versions.map((v) => v.version_number)).toEqual([2]);
    expect(store().versionsTotal).toBe(1);
    expect(store().documents[0].version_count).toBe(1);
    // Deleting a version that is not open touches neither the editor nor dirty.
    expect(store().versionNumber).toBe(2);
    expect(store().content).toBe("<p>doc 1 v2</p>");
    expect(store().dirty).toBe(true);
    expect(getDocument).not.toHaveBeenCalled();
  });

  // The DoD case: deleting the OPEN version must not leave the UI pointing at a
  // version that no longer exists. It falls back exactly like a first open —
  // re-asking the server for the newest remaining version.
  it("deleting the open version reopens the document on the newest survivor", async () => {
    apiDeleteVersion.mockResolvedValue(undefined);
    getDocument.mockResolvedValue(detail(1, 1, 1));
    listVersions.mockResolvedValue(versionPage([1]));
    getVersion.mockResolvedValue(version(1, 1));
    useDocumentStore.setState({
      documentId: 1,
      versionNumber: 2,
      versionName: "Version 2",
      versions: [summary(2), summary(1)],
      versionsTotal: 2,
      content: "<p>doc 1 v2</p>",
      dirty: true,
    });

    await expect(store().deleteVersion(2)).resolves.toBe(true);

    expect(apiDeleteVersion).toHaveBeenCalledWith(1, 2);
    // selectDocument(1) ran and landed on the version the server now names newest.
    expect(getDocument).toHaveBeenCalledWith(1);
    expect(store().versionNumber).toBe(1);
    expect(store().content).toBe("<p>doc 1 v1</p>");
    expect(store().dirty).toBe(false);
  });

  it("reports the server's 409 and changes nothing", async () => {
    apiDeleteVersion.mockRejectedValue(
      new ApiError(409, "Cannot delete the only version of a patent."),
    );
    useDocumentStore.setState({
      documentId: 1,
      versionNumber: 1,
      versions: [summary(1)],
      versionsTotal: 1,
    });

    await expect(store().deleteVersion(1)).resolves.toBe(false);

    expect(store().error).toBe("Cannot delete the only version of a patent.");
    expect(store().versions).toHaveLength(1);
    expect(store().versionsTotal).toBe(1);
  });

  it("refuses with nothing open and calls no api helper", async () => {
    await expect(store().deleteVersion(1)).resolves.toBe(false);
    expect(store().error).toBe("There is no open patent.");
    expect(apiDeleteVersion).not.toHaveBeenCalled();
  });

  it("pendingAction names the delete in flight and clears when it resolves", async () => {
    let finish: (() => void) | undefined;
    apiDeleteVersion.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve;
        }),
    );
    useDocumentStore.setState({
      documentId: 1,
      versionNumber: 2,
      versions: [summary(2), summary(1)],
      versionsTotal: 2,
    });

    const deleting = store().deleteVersion(1);
    expect(store().pendingAction).toBe("deleteVersion");
    expect(store().saving).toBe(true);

    finish!();
    await deleting;

    expect(store().pendingAction).toBeNull();
    expect(store().saving).toBe(false);
  });
});

describe("editor identity", () => {
  // S7
  it("clearEditor(staleEditor) does not null the live editor", () => {
    const stale = fakeEditor("<p>old</p>");
    const live = fakeEditor("<p>new</p>");

    store().setEditor(stale);
    store().setEditor(live); // the new child's onCreate runs first...
    store().clearEditor(stale); // ...then the old child's onDestroy

    expect(store().editor).toBe(live);

    store().clearEditor(live);
    expect(store().editor).toBeNull();
  });
});
