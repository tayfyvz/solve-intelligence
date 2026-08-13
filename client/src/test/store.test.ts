import type { Editor } from "@tiptap/core";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { DocumentDetail, VersionRead } from "../types";

// The store's only dependency is the api module, so one `vi.mock` isolates it
// completely. `toMessage` and `ApiError` are kept real — they are pure, and the
// error text the store surfaces should be the text the app really shows.
vi.mock("../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api")>()),
  listDocuments: vi.fn(),
  getDocument: vi.fn(),
  getVersion: vi.fn(),
  createVersion: vi.fn(),
  updateVersion: vi.fn(),
}));

const { createVersion, getDocument, getVersion, listDocuments, updateVersion } = vi.mocked(
  await import("../api"),
);
const { useDocumentStore } = await import("../store");

const store = () => useDocumentStore.getState();

const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function detail(id: number, versions = [1]): DocumentDetail {
  return {
    id,
    title: `Patent ${id}`,
    versions: versions.map((n) => ({
      version_number: n,
      updated_at: `2026-01-0${n}T00:00:00`,
    })),
  };
}

function version(id: number, n: number, content = `<p>doc ${id} v${n}</p>`): VersionRead {
  return { document_id: id, version_number: n, content, updated_at: `2026-01-0${n}T00:00:00` };
}

/**
 * The store only ever calls `getHTML()` on the editor, so a two-field fake is
 * the whole contract. Mounting TipTap in jsdom to assert this would test jsdom.
 */
function fakeEditor(html = "<p>live</p>"): Editor {
  return { getHTML: () => html } as unknown as Editor;
}

// The store itself is reset in `test/setup.ts`'s afterEach.
beforeEach(() => {
  vi.mocked(listDocuments).mockReset();
  vi.mocked(getDocument).mockReset();
  vi.mocked(getVersion).mockReset();
  vi.mocked(createVersion).mockReset();
  vi.mocked(updateVersion).mockReset();
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
  // guard that has to catch it is the one after `getVersion`. This is the more
  // likely shape live — the detail is small and the content is large.
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

  // S8 — Task 1 requirement 2's only client-side proof.
  it("selectVersion loads that version's content and clears dirty", async () => {
    getVersion.mockResolvedValue(version(1, 1, "<p>older draft</p>"));
    useDocumentStore.setState({ documentId: 1, versionNumber: 3, dirty: true });

    await store().selectVersion(1);

    expect(getVersion).toHaveBeenCalledWith(1, 1);
    expect(store().versionNumber).toBe(1);
    expect(store().content).toBe("<p>older draft</p>");
    expect(store().dirty).toBe(false);
  });

  // S8, staleness half: the version dropdown is the easiest control in the app
  // to click twice quickly.
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

  // S6
  it("a fresh selection clears the dirty flag", async () => {
    getDocument.mockResolvedValue(detail(1));
    getVersion.mockResolvedValue(version(1, 1));
    useDocumentStore.setState({ dirty: true });

    await store().selectDocument(1);

    expect(store().dirty).toBe(false);
  });

  it("reports a document with no versions instead of asking for version 0", async () => {
    getDocument.mockResolvedValue({ id: 9, title: "Empty patent", versions: [] });

    await store().selectDocument(9);

    expect(getVersion).not.toHaveBeenCalled();
    expect(store().error).toBe('"Empty patent" has no saved versions.');
    expect(store().loading).toBe(false);
  });

  // Not in the §11 table: `loadDocuments`' auto-select is the reason the first
  // paint is not an empty editor column, and nothing else covers it.
  it("loadDocuments selects the first document when none is selected", async () => {
    listDocuments.mockResolvedValue([
      { id: 4, title: "Patent 4" },
      { id: 5, title: "Patent 5" },
    ]);
    getDocument.mockResolvedValue(detail(4, [1, 2]));
    getVersion.mockResolvedValue(version(4, 2));

    await store().loadDocuments();

    expect(store().documents).toHaveLength(2);
    expect(store().documentId).toBe(4);
    // The highest version number, not the first: that is the newest draft.
    expect(getVersion).toHaveBeenCalledWith(4, 2);
    expect(store().versionNumber).toBe(2);
    expect(store().loading).toBe(false);
  });
});

describe("saving", () => {
  beforeEach(() => {
    useDocumentStore.setState({
      editor: fakeEditor(),
      documentId: 1,
      versionNumber: 2,
      // Two versions, so "refresh the saved one" is distinguishable from
      // "refresh them all".
      versions: [
        { version_number: 1, updated_at: "2026-01-01T00:00:00" },
        { version_number: 2, updated_at: "2026-01-02T00:00:00" },
      ],
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
    // otherwise the dropdown shows a stale time forever.
    expect(store().versions).toEqual([
      { version_number: 1, updated_at: "2026-01-01T00:00:00" },
      { version_number: 2, updated_at: "2026-03-03T00:00:00" },
    ]);
    expect(store().versionNumber).toBe(2);
    expect(store().dirty).toBe(false);
    expect(store().saving).toBe(false);
  });

  // S4
  it("saveAsNewVersion() appends the returned version and selects it", async () => {
    createVersion.mockResolvedValue({
      document_id: 1,
      version_number: 3,
      content: "<p>sanitised by nh3</p>",
      updated_at: "2026-02-02T00:00:00",
    });

    await expect(store().saveAsNewVersion()).resolves.toBe(true);

    expect(createVersion).toHaveBeenCalledWith(1, "<p>live</p>");
    expect(store().versions).toEqual([
      { version_number: 1, updated_at: "2026-01-01T00:00:00" },
      { version_number: 2, updated_at: "2026-01-02T00:00:00" },
      { version_number: 3, updated_at: "2026-02-02T00:00:00" },
    ]);
    expect(store().versionNumber).toBe(3);
    // From the 201 body: the server's sanitised echo, not the editor's HTML.
    expect(store().content).toBe("<p>sanitised by nh3</p>");
    expect(store().dirty).toBe(false);
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
  // not append the version, must not move `versionNumber`, and must not clear
  // the new document's dirty flag.
  it("a saveAsNewVersion resolving after a switch is discarded entirely", async () => {
    createVersion.mockImplementation(async () => {
      await delay(50);
      return { document_id: 1, version_number: 3, content: "<p>late</p>", updated_at: "2026-04-04" };
    });
    getDocument.mockResolvedValue(detail(2));
    getVersion.mockResolvedValue(version(2, 1));

    const saving = store().saveAsNewVersion();
    await store().selectDocument(2);
    store().setDirty(true);

    await expect(saving).resolves.toBe(false);
    expect(store().documentId).toBe(2);
    expect(store().versions).toEqual(detail(2).versions); // nothing appended
    expect(store().versionNumber).toBe(1);
    expect(store().content).toBe("<p>doc 2 v1</p>");
    expect(store().dirty).toBe(true);
    expect(store().saving).toBe(false);
  });

  it("reports a failed save instead of clearing the dirty flag", async () => {
    updateVersion.mockRejectedValue(new Error("Version 2 of document 1 was not found."));

    await expect(store().save()).resolves.toBe(false);

    expect(store().error).toBe("Version 2 of document 1 was not found.");
    expect(store().dirty).toBe(true);
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
