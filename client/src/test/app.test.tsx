import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Editor } from "@tiptap/core";

import type { DocumentDetail, DocumentPage, VersionPage, VersionRead } from "../types";

// One mock for the whole seam. `toMessage` and `ApiError` stay real: the first
// test asserts that what the UI shows is what `toMessage` produced.
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
}));

// TipTap has its own tests and mounting it here would test jsdom, not App. The
// stub keeps App's own logic — selection, dialogs, errors — in view.
//
// It reads `content` once, at mount, exactly as the real uncontrolled editor
// does, so an assertion about what is on screen after a switch means the same
// thing here as it does in the browser.
vi.mock("../components/Editor", async () => {
  const { useState } = await import("react");
  function EditorStub({ content }: { content: string }) {
    const [mountedWith] = useState(content);
    return <div data-testid="editor">{mountedWith}</div>;
  }
  return { default: EditorStub };
});

const {
  ApiError,
  toMessage,
  createDocument,
  createVersion,
  getDocument,
  getVersion,
  listDocuments,
  listVersions,
  renameDocument,
  renameVersion,
  updateVersion,
} = vi.mocked(await import("../api"));
const { useDocumentStore, PAGE_SIZE } = await import("../store");
const { default: App } = await import("../App");

const store = () => useDocumentStore.getState();
const STAMP = "2026-01-01T09:30:00";

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

function documentPage(ids: number[], extra: Partial<DocumentPage> = {}): DocumentPage {
  return {
    items: ids.map((id) => ({ id, title: `Patent ${id}`, version_count: 1, updated_at: STAMP })),
    total: ids.length,
    limit: PAGE_SIZE,
    offset: 0,
    ...extra,
  };
}

/** Versions come back newest first — the order the panel must preserve. */
function versionPage(numbers: number[], extra: Partial<VersionPage> = {}): VersionPage {
  const items = [...numbers]
    .sort((a, b) => b - a)
    .map((n) => ({
      version_number: n,
      name: `Version ${n}`,
      created_at: STAMP,
      updated_at: STAMP,
    }));
  return { items, total: items.length, limit: PAGE_SIZE, offset: 0, ...extra };
}

function version(id: number, n: number, content = `<p>doc ${id} v${n}</p>`): VersionRead {
  return {
    document_id: id,
    version_number: n,
    name: `Version ${n}`,
    content,
    created_at: STAMP,
    updated_at: STAMP,
  };
}

/**
 * The store only ever calls `getHTML()`. The plugin lifecycle is `ChatPanel`'s, which
 * App now mounts beside the editor — a stub without it throws out of an effect and
 * fails every test in this file for a reason that has nothing to do with the shell.
 */
function fakeEditor(html = "<p>live</p>"): Editor {
  return {
    getHTML: () => html,
    isDestroyed: false,
    registerPlugin: () => {},
    unregisterPlugin: () => {},
    on: () => {},
    off: () => {},
  } as unknown as Editor;
}

/** A tree row, not the pencil next to it: its name starts with the row's title. */
const rowNamed = (label: string) =>
  screen.getByRole("button", { name: new RegExp(`^${label}\\b`) });

/** The app bar, which now carries what is open and the write actions. */
const banner = () => screen.getByRole("banner");

/** The app as the user finds it: one patent open on its newest version. */
async function renderOpenDocument(numbers = [1], ids = [1]) {
  listDocuments.mockResolvedValue(documentPage(ids));
  getDocument.mockImplementation(async (id: number) =>
    detail(id, numbers.length, Math.max(...numbers)),
  );
  listVersions.mockImplementation(async () => versionPage(numbers));
  getVersion.mockImplementation(async (id: number, n: number) => version(id, n));
  useDocumentStore.setState({ editor: fakeEditor() });

  render(<App />);
  await waitFor(() => expect(store().documentId).toBe(ids[0]));
  await screen.findByTestId("editor");
}

beforeEach(() => {
  vi.mocked(listDocuments).mockReset();
  vi.mocked(createDocument).mockReset();
  vi.mocked(getDocument).mockReset();
  vi.mocked(renameDocument).mockReset();
  vi.mocked(listVersions).mockReset();
  vi.mocked(getVersion).mockReset();
  vi.mocked(createVersion).mockReset();
  vi.mocked(updateVersion).mockReset();
  vi.mocked(renameVersion).mockReset();
});

describe("App shell", () => {
  // The inherited App only ever `console.error`d its failures; the sentence has
  // to reach the screen. Asserting against `toMessage`'s own output — rather than
  // a literal — makes this a test of the wiring, not of a string.
  it("renders the message from toMessage when the load fails", async () => {
    const failure = Object.assign(new Error("Request failed with status code 404"), {
      isAxiosError: true,
      response: { status: 404, data: { detail: "Patent 1 was not found." } },
    });
    const expected = toMessage(failure);
    listDocuments.mockRejectedValue(new ApiError(404, expected));

    render(<App />);

    const banner = await screen.findByRole("alert");
    expect(expected).toBe("Patent 1 was not found.");
    expect(banner.textContent).toContain(expected);

    // And it is dismissible, or a transient failure sits on screen forever.
    await userEvent.click(within(banner).getByRole("button", { name: "Dismiss error" }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });

  // The dialog is the most stateful thing in the client — four outcomes over a
  // selection that must not move until one of them says so. Cancel first, then
  // Discard on the *same* pending selection: reopening after a cancel is the path
  // that breaks if `pending` is cleared in the wrong place.
  it("switching while dirty: Cancel leaves the selection untouched, Discard commits it", async () => {
    const user = userEvent.setup();
    await renderOpenDocument([1], [1, 2]);

    // The user types: Editor.onUpdate is the only writer of this flag.
    act(() => store().setDirty(true));
    // An error from an earlier request must not open the dialog looking like a
    // save that has already failed.
    act(() => useDocumentStore.setState({ error: "Cannot reach the server." }));

    await user.click(rowNamed("Patent 2"));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByRole("alert")).toBeNull();
    // The copy names the version: with two save buttons, "unsaved changes"
    // without a target does not say what Save would overwrite.
    expect(dialog.textContent).toContain("version 1");

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(store().documentId).toBe(1);
    expect(store().dirty).toBe(true);
    expect(getDocument).toHaveBeenCalledTimes(1); // Cancel means it never happened

    await user.click(rowNamed("Patent 2"));
    await user.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: "Discard changes" }),
    );

    await waitFor(() => expect(store().documentId).toBe(2));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(store().dirty).toBe(false);
    await waitFor(() => expect(screen.getByTestId("editor").textContent).toBe("<p>doc 2 v1</p>"));
  });

  // The dialog's other two outcomes. Both save buttons only *start* the save; the
  // held-back switch is committed by App's effect when the document stops being
  // dirty. Without this test that whole mechanism — and requirement 3 on the
  // switch path — is covered by nothing.
  it("switching while dirty: Save persists in place and then commits the switch", async () => {
    const user = userEvent.setup();
    await renderOpenDocument([1], [1, 2]);
    updateVersion.mockResolvedValue(version(1, 1));
    act(() => store().setDirty(true));

    await user.click(rowNamed("Patent 2"));
    await user.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: "Save" }),
    );

    await waitFor(() => expect(store().documentId).toBe(2));
    expect(updateVersion).toHaveBeenCalledWith(1, 1, "<p>live</p>");
    expect(createVersion).not.toHaveBeenCalled(); // requirement 3
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(store().dirty).toBe(false);
  });

  // A failed save must not close the dialog: the switch is still pending and the
  // reason has to be readable next to the buttons that caused it.
  it("switching while dirty: a failed save keeps the dialog open with the error inside", async () => {
    const user = userEvent.setup();
    await renderOpenDocument([1], [1, 2]);
    updateVersion.mockRejectedValue(new ApiError(500, "The server could not save that."));
    act(() => store().setDirty(true));

    await user.click(rowNamed("Patent 2"));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(within(screen.getByRole("dialog")).getByRole("alert").textContent).toBe(
        "The server could not save that.",
      ),
    );
    expect(store().documentId).toBe(1);
    expect(store().dirty).toBe(true);

    // Still dismissible afterwards: a failed save must not trap the user in a
    // dialog whose only working button is the one that just failed.
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(store().documentId).toBe(1);
  });

  // Dismissal is inert while the save runs: Escape or a backdrop click mid-save
  // would drop the pending switch, and when the save landed the user would get
  // neither the switch they asked for nor an explanation.
  it("ignores Escape and disables every outcome while a save is in flight", async () => {
    const user = userEvent.setup();
    await renderOpenDocument([1], [1, 2]);
    updateVersion.mockReturnValue(new Promise<VersionRead>(() => {}));
    act(() => store().setDirty(true));

    await user.click(rowNamed("Patent 2"));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(store().saving).toBe(true));
    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog")).toBeTruthy();
    for (const name of ["Save", "Save as new version", "Discard changes", "Cancel"]) {
      expect((within(dialog).getByRole("button", { name }) as HTMLButtonElement).disabled).toBe(
        true,
      );
    }

    // Land the never-resolving save and close the dialog before the test ends:
    // the store is a module singleton, and a switch still pending here would be
    // committed by the teardown that clears `dirty`, leaking into the next test.
    act(() => useDocumentStore.setState({ saving: false, dirty: false }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  // The panels are "extendable" per the brief; the collapse toggle at minimum has
  // to be a real, keyboard-operable button rather than a click handler on a div.
  it("collapses and expands a side panel from the keyboard", async () => {
    const user = userEvent.setup();
    await renderOpenDocument([1]);

    screen.getByRole("button", { name: "Collapse Patents panel" }).focus();
    await user.keyboard("{Enter}");

    expect(screen.queryByRole("heading", { name: "Patents" })).toBeNull();
    const expand = screen.getByRole("button", { name: "Expand Patents panel" });
    expect(expand.getAttribute("aria-expanded")).toBe("false");

    expand.focus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("heading", { name: "Patents" })).toBeTruthy();
  });

  // The sidebar is one tree: only the open patent carries a branch, and opening
  // another patent moves that branch rather than leaving two expanded.
  it("nests the versions under the open patent and moves them when another opens", async () => {
    const user = userEvent.setup();
    await renderOpenDocument([1, 2], [1, 2]);

    const branch = screen.getByRole("list", { name: 'Versions of "Patent 1"' });
    expect(within(branch).getByRole("button", { name: /^Version 2\b/ })).toBeTruthy();
    expect(screen.queryByRole("list", { name: 'Versions of "Patent 2"' })).toBeNull();

    await user.click(rowNamed("Patent 2"));

    await waitFor(() => expect(store().documentId).toBe(2));
    expect(screen.getByRole("list", { name: 'Versions of "Patent 2"' })).toBeTruthy();
    expect(screen.queryByRole("list", { name: 'Versions of "Patent 1"' })).toBeNull();
  });

  // The many-versions case. "Show more" appends rather than paging, so version 45
  // must still be on screen next to version 25 — and appending is not a selection,
  // so it must not raise the dirty dialog or touch the editor.
  it("appends the next page of versions without disturbing the editor", async () => {
    const user = userEvent.setup();
    const firstPage = Array.from({ length: 20 }, (_, i) => 45 - i); // 45…26
    const secondPage = Array.from({ length: 20 }, (_, i) => 25 - i); // 25…6

    listDocuments.mockResolvedValue(documentPage([1]));
    getDocument.mockResolvedValue(detail(1, 45, 45));
    listVersions.mockImplementation(async (_id: number, _limit: number, offset: number) =>
      versionPage(offset === 0 ? firstPage : secondPage, { total: 45, offset }),
    );
    getVersion.mockImplementation(async (id: number, n: number) => version(id, n));
    useDocumentStore.setState({ editor: fakeEditor() });

    render(<App />);
    await screen.findByTestId("editor");
    act(() => store().setDirty(true));

    expect(screen.getByText("20 of 45")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Version 25\b/ })).toBeNull();

    await user.click(screen.getByRole("button", { name: "Show more versions" }));

    await waitFor(() => expect(screen.getByText("40 of 45")).toBeTruthy());
    expect(listVersions).toHaveBeenLastCalledWith(1, PAGE_SIZE, 20);
    expect(screen.getByRole("button", { name: /^Version 25\b/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /^Version 45\b/ })).toBeTruthy(); // page 1 kept
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(store().dirty).toBe(true);
    expect(store().versionNumber).toBe(45);
  });

  // Requirement 2, through the control the user actually touches: the version
  // rows that replaced the <select>.
  it("switching version from the version list loads that version", async () => {
    const user = userEvent.setup();
    await renderOpenDocument([1, 2]);
    expect(store().versionNumber).toBe(2);

    await user.click(rowNamed("Version 1"));

    await waitFor(() => expect(store().versionNumber).toBe(1));
    expect(getVersion).toHaveBeenCalledWith(1, 1);
    await waitFor(() => expect(screen.getByTestId("editor").textContent).toBe("<p>doc 1 v1</p>"));
    expect(rowNamed("Version 1").getAttribute("aria-current")).toBe("true");
  });

  // Requirement 1, and the only test that would catch someone dropping
  // `versionNumber` from the editor's `key={documentId}:{versionNumber}`: this
  // path never sets `loading`, so nothing else remounts the editor.
  it("save as new version remounts the editor on the new version", async () => {
    const user = userEvent.setup();
    await renderOpenDocument([1]);
    act(() => store().setDirty(true));
    createVersion.mockResolvedValue(version(1, 2, "<p>created v2</p>"));
    listVersions.mockImplementation(async () => versionPage([1, 2]));

    await user.click(screen.getByRole("button", { name: "Save as new version" }));

    await waitFor(() => expect(store().versionNumber).toBe(2));
    // The third argument is the version name, which only ChatPanel ever supplies;
    // `null` is what `createVersion` defaulted to before it was threaded through.
    expect(createVersion).toHaveBeenCalledWith(1, "<p>live</p>", null);
    // The server's sanitised echo, reached by remount rather than a prop write.
    await waitFor(() => expect(screen.getByTestId("editor").textContent).toBe("<p>created v2</p>"));
    expect(screen.queryByText("Unsaved changes")).toBeNull();
  });

  // The confirmed repro for the stale sidebar: the version IS created, but the
  // list below the patent row kept showing two rows next to "3 versions" until a
  // full page reload — because App commits the held-back switch the instant
  // `dirty` clears, and that switch bumped the token the list fetch was captured
  // against. Its response was dropped, and `listLoading` stuck on with it.
  it("switching while dirty: Save as new version shows the new version in the list", async () => {
    const user = userEvent.setup();
    await renderOpenDocument([1, 2]);
    act(() => store().setDirty(true));
    createVersion.mockResolvedValue(version(1, 3, "<p>created v3</p>"));
    listVersions.mockImplementation(async () => versionPage([1, 2, 3]));

    await user.click(rowNamed("Version 1"));
    await user.click(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: "Save as new version",
      }),
    );

    // The held-back switch went through, so the user is on version 1…
    await waitFor(() => expect(store().versionNumber).toBe(1));
    expect(screen.queryByRole("dialog")).toBeNull();
    // …and the version they just created is in the sidebar, not missing until a
    // reload.
    expect(rowNamed("Version 3")).toBeTruthy();
    expect(store().versions.map((v) => v.version_number)).toEqual([3, 2, 1]);
    // The list controls are usable afterwards.
    expect(store().listLoading).toBe(false);
  });

  // The dirty-conditional buttons, and where they live: from a clean state "Save"
  // would be a no-op, so the only honest write left is a copy. All of it is in the
  // app bar now, alongside the patent and version it would write to.
  it("swaps the app bar's save buttons for Duplicate when there is nothing to save", async () => {
    await renderOpenDocument([1]);
    const inBanner = (name: string) => within(banner()).queryByRole("button", { name });

    expect(within(banner()).getByRole("heading", { name: "Patent 1" })).toBeTruthy();
    expect(within(banner()).getByText("Version 1")).toBeTruthy();
    expect(inBanner("Duplicate this version")).toBeTruthy();
    expect(inBanner("Save")).toBeNull();
    expect(inBanner("Save as new version")).toBeNull();
    expect(within(banner()).queryByText("Unsaved changes")).toBeNull();

    act(() => store().setDirty(true));

    expect(inBanner("Save")).toBeTruthy();
    expect(inBanner("Save as new version")).toBeTruthy();
    expect(inBanner("Duplicate this version")).toBeNull();
    expect(within(banner()).getByText("Unsaved changes")).toBeTruthy();
  });

  // The client half of the concurrent-write race the server answers with a 409: a
  // second click while the first write is in flight must not be possible, and
  // only the pressed button may spin.
  it("disables both save buttons while a save is in flight, spinning only the one pressed", async () => {
    const user = userEvent.setup();
    await renderOpenDocument([1]);
    act(() => store().setDirty(true));
    updateVersion.mockReturnValue(new Promise<VersionRead>(() => {}));

    await user.click(screen.getByRole("button", { name: "Save" }));

    const save = () => screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;
    const saveAsNew = () =>
      screen.getByRole("button", { name: "Save as new version" }) as HTMLButtonElement;
    await waitFor(() => expect(save().disabled).toBe(true));
    expect(saveAsNew().disabled).toBe(true);
    expect(within(save()).getByRole("status", { name: "Loading" })).toBeTruthy();
    expect(within(saveAsNew()).queryByRole("status")).toBeNull();
    expect(createVersion).not.toHaveBeenCalled();
  });

  // Creating a patent, including the 409 the server returns for a duplicate
  // title. The dialog has to stay open with the reason *and* the typed content.
  it("creates a patent, keeping a rejected title in the dialog with the reason", async () => {
    const user = userEvent.setup();
    await renderOpenDocument([1]);
    createDocument.mockRejectedValueOnce(new ApiError(409, 'A patent called "Widget" exists.'));

    await user.click(screen.getByRole("button", { name: "New patent" }));
    await user.type(screen.getByLabelText("Title"), "  Widget  ");
    await user.type(screen.getByLabelText(/Starting content/), "<p>seed</p>");
    await user.click(screen.getByRole("button", { name: "Create patent" }));

    // Trimmed on the way out, and the failure lands in the dialog, not the banner.
    expect(createDocument).toHaveBeenCalledWith("Widget", "<p>seed</p>");
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("alert").textContent).toBe('A patent called "Widget" exists.');
    expect((screen.getByLabelText("Title") as HTMLInputElement).value).toBe("  Widget  ");

    // Second attempt succeeds: the dialog closes and the new patent is opened.
    createDocument.mockResolvedValue(detail(2));
    listDocuments.mockResolvedValue(documentPage([1, 2]));
    await user.click(screen.getByRole("button", { name: "Create patent" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(store().documentId).toBe(2);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("disables Create while the title is empty", async () => {
    const user = userEvent.setup();
    await renderOpenDocument([1]);

    await user.click(screen.getByRole("button", { name: "New patent" }));
    const create = () => screen.getByRole("button", { name: "Create patent" }) as HTMLButtonElement;
    expect(create().disabled).toBe(true);

    await user.type(screen.getByLabelText("Title"), "   ");
    expect(create().disabled).toBe(true); // whitespace is not a title

    await user.type(screen.getByLabelText("Title"), "Widget");
    expect(create().disabled).toBe(false);
  });

  // Creating opens the new patent, so it replaces what is in the editor exactly
  // like clicking another row — and must go behind the same guard.
  it("puts creating a patent behind the dirty dialog", async () => {
    const user = userEvent.setup();
    await renderOpenDocument([1]);
    act(() => store().setDirty(true));

    await user.click(screen.getByRole("button", { name: "New patent" }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toContain("Unsaved changes");
    expect(screen.queryByLabelText("Title")).toBeNull();

    await user.click(within(dialog).getByRole("button", { name: "Discard changes" }));
    expect(await screen.findByLabelText("Title")).toBeTruthy();
  });

  // Paging is not a selection: it never touches the editor, so it must not raise
  // the dirty dialog — and it must ask for the offset it advertises.
  it("pages the patent list without disturbing the editor", async () => {
    const user = userEvent.setup();
    listDocuments.mockResolvedValue(documentPage([1, 2], { total: 45 }));
    getDocument.mockImplementation(async (id: number) => detail(id));
    listVersions.mockImplementation(async () => versionPage([1]));
    getVersion.mockImplementation(async (id: number, n: number) => version(id, n));

    render(<App />);
    await waitFor(() => expect(store().documentId).toBe(1));
    act(() => store().setDirty(true));

    expect(screen.getByText("1–20 of 45")).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "Previous page of patents" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);

    await user.click(screen.getByRole("button", { name: "Next page of patents" }));

    await waitFor(() => expect(listDocuments).toHaveBeenLastCalledWith(PAGE_SIZE, PAGE_SIZE));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(store().dirty).toBe(true);
    expect(store().documentId).toBe(1);
  });

  // Renaming the open version has to reach the header, not just the list row.
  it("renames the open version inline and updates the header", async () => {
    const user = userEvent.setup();
    await renderOpenDocument([1]);
    renameVersion.mockRejectedValueOnce(new ApiError(409, 'A version called "Final" exists.'));

    await user.click(screen.getByRole("button", { name: "Rename version 1" }));
    const field = screen.getByRole("textbox", { name: "Rename version 1" });
    await user.clear(field);
    await user.type(field, "Final{Enter}");

    // Next to the field, not in the app-wide banner at the other end of the page.
    const form = field.closest("form") as HTMLFormElement;
    await waitFor(() =>
      expect(within(form).getByRole("alert").textContent).toBe('A version called "Final" exists.'),
    );
    expect(store().error).toBeNull();

    renameVersion.mockResolvedValue({ ...version(1, 1), name: "Final" });
    await user.click(within(form).getByRole("button", { name: "Save name" }));

    await waitFor(() => expect(store().versionName).toBe("Final"));
    expect(within(banner()).getByText("Final")).toBeTruthy();
  });

  it("renames a patent inline and updates the app bar", async () => {
    const user = userEvent.setup();
    await renderOpenDocument([1]);
    renameDocument.mockResolvedValue({ ...detail(1), title: "Widget improvements" });
    listDocuments.mockResolvedValue(
      documentPage([1], { items: [{ id: 1, title: "Widget improvements", version_count: 1, updated_at: STAMP }] }),
    );

    await user.click(screen.getByRole("button", { name: 'Rename patent "Patent 1"' }));
    const field = screen.getByRole("textbox", { name: 'Rename patent "Patent 1"' });
    await user.clear(field);
    await user.type(field, "Widget improvements{Enter}");

    await waitFor(() => expect(renameDocument).toHaveBeenCalledWith(1, "Widget improvements"));
    await waitFor(() =>
      expect(within(banner()).getByText("Widget improvements")).toBeTruthy(),
    );
  });

  // A malformed body used to reach `documents.map` and paint a white screen — the
  // one failure mode with nothing at all to read.
  it("shows a message instead of a white screen when the document list is malformed", async () => {
    listDocuments.mockResolvedValue({ items: [] } as unknown as DocumentPage);

    render(<App />);

    const banner = await screen.findByRole("alert");
    expect(banner.textContent).toContain("The server returned an unreadable list of patents.");
    expect(screen.getByText("No patents yet. Create one to get started.")).toBeTruthy();
  });

  // "" is falsy and App guards the editor on `content !== null`: a saved-empty
  // version must still open.
  it('mounts the editor for a version whose content is ""', async () => {
    listDocuments.mockResolvedValue(documentPage([1]));
    getDocument.mockImplementation(async (id: number) => detail(id));
    listVersions.mockImplementation(async () => versionPage([1]));
    getVersion.mockResolvedValue({ ...version(1, 1), content: "" });

    render(<App />);

    await waitFor(() => expect(store().content).toBe(""));
    expect(await screen.findByTestId("editor")).toBeTruthy();
  });

  // The "whole page reloads" complaint: the editor must stay mounted across a
  // switch, dimmed, rather than being replaced by a skeleton and mounted again.
  it("keeps the editor mounted while the next version loads", async () => {
    const user = userEvent.setup();
    await renderOpenDocument([1, 2]);
    let release: (value: VersionRead) => void = () => {};
    getVersion.mockReturnValueOnce(
      new Promise<VersionRead>((resolve) => {
        release = resolve;
      }),
    );

    await user.click(rowNamed("Version 1"));

    // Mid-flight: the old content is still on screen, and marked busy.
    await waitFor(() => expect(store().loading).toBe(true));
    expect(screen.getByTestId("editor").textContent).toBe("<p>doc 1 v2</p>");
    expect(screen.queryByRole("status", { name: "Loading document" })).toBeNull();

    act(() => release(version(1, 1)));
    await waitFor(() => expect(screen.getByTestId("editor").textContent).toBe("<p>doc 1 v1</p>"));
  });
});
