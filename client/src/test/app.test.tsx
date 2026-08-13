import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Editor } from "@tiptap/core";

import type { DocumentDetail, DocumentSummary, VersionRead } from "../types";

// One mock for the whole seam. `toMessage` and `ApiError` stay real: this file's
// first test asserts that what the UI shows is what `toMessage` produced.
vi.mock("../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api")>()),
  listDocuments: vi.fn(),
  getDocument: vi.fn(),
  getVersion: vi.fn(),
  createVersion: vi.fn(),
  updateVersion: vi.fn(),
}));

// TipTap has its own tests (E1/E2) and mounting it here would test jsdom, not
// App. The stub keeps App's own logic — selection, the dirty dialog, errors — in
// view.
//
// It reads `content` once, at mount, exactly as the real uncontrolled editor
// does — so an assertion about what is on screen after a switch means the same
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
  createVersion,
  getDocument,
  getVersion,
  listDocuments,
  updateVersion,
} = await import("../api");
const { useDocumentStore } = await import("../store");
const { default: App } = await import("../App");

const store = () => useDocumentStore.getState();

function detail(id: number, versions = [1]): DocumentDetail {
  return {
    id,
    title: `Patent ${id}`,
    versions: versions.map((n) => ({ version_number: n, updated_at: "2026-01-01T09:30:00" })),
  };
}

function version(id: number, n: number): VersionRead {
  return {
    document_id: id,
    version_number: n,
    content: `<p>doc ${id} v${n}</p>`,
    updated_at: "2026-01-01T09:30:00",
  };
}

/** The app as the user finds it: one patent open on its newest version. */
async function renderOpenDocument(versions = [1]) {
  vi.mocked(listDocuments).mockResolvedValue([{ id: 1, title: "Patent 1" }]);
  vi.mocked(getDocument).mockImplementation(async (id: number) => detail(id, versions));
  vi.mocked(getVersion).mockImplementation(async (id: number, n: number) => version(id, n));
  useDocumentStore.setState({ editor: fakeEditor() });

  render(<App />);
  await waitFor(() => expect(store().documentId).toBe(1));
  await screen.findByTestId("editor");
}

/** The store only ever calls `getHTML()` on the editor — that is the contract. */
function fakeEditor(html = "<p>live</p>"): Editor {
  return { getHTML: () => html } as unknown as Editor;
}

beforeEach(() => {
  vi.mocked(listDocuments).mockReset();
  vi.mocked(getDocument).mockReset();
  vi.mocked(getVersion).mockReset();
  vi.mocked(createVersion).mockReset();
  vi.mocked(updateVersion).mockReset();
});

describe("App shell", () => {
  // D1. The inherited App only ever `console.error`d its failures; CLAUDE.md
  // requires the sentence to reach the screen. Asserting against `toMessage`'s
  // own output — rather than a hardcoded string — is what makes this a test of
  // the wiring instead of a test of a literal.
  it("renders the message from toMessage when the load fails", async () => {
    const failure = Object.assign(new Error("Request failed with status code 404"), {
      isAxiosError: true,
      response: { status: 404, data: { detail: "Document 1 not found." } },
    });
    const expected = toMessage(failure);
    // What the real `listDocuments` throws for that response.
    vi.mocked(listDocuments).mockRejectedValue(new ApiError(404, expected));

    render(<App />);

    const banner = await screen.findByRole("alert");
    expect(expected).toBe("Document 1 not found.");
    expect(banner.textContent).toContain(expected);

    // And it is dismissible, or a transient failure sits on screen forever.
    await userEvent.click(within(banner).getByRole("button", { name: "Dismiss error" }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });

  // D2. The dialog is the most stateful thing in the client — four outcomes over
  // a selection that must not move until one of them says so. Cancel first, then
  // Discard on the *same* pending selection: reopening after a cancel is the path
  // that breaks if `pending` is cleared in the wrong place.
  it("switching while dirty: Cancel leaves the selection untouched, Discard commits it", async () => {
    const user = userEvent.setup();
    vi.mocked(listDocuments).mockResolvedValue([
      { id: 1, title: "Patent 1" },
      { id: 2, title: "Patent 2" },
    ]);
    vi.mocked(getDocument).mockImplementation(async (id: number) => detail(id));
    vi.mocked(getVersion).mockImplementation(async (id: number, n: number) => version(id, n));

    render(<App />);
    await waitFor(() => expect(store().documentId).toBe(1));
    expect(await screen.findByTestId("editor")).toBeTruthy();

    // The user types: Editor.onUpdate is the only writer of this flag.
    act(() => store().setDirty(true));

    // An error from an earlier request must not open the dialog looking like a
    // save that has already failed.
    act(() => useDocumentStore.setState({ error: "Cannot reach the server." }));

    await user.click(screen.getByRole("button", { name: "Patent 2" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByRole("alert")).toBeNull();
    // The copy names the version, because "unsaved changes" without a target
    // does not say what Save would overwrite.
    expect(dialog.textContent).toContain("version 1");

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(store().documentId).toBe(1);
    expect(store().dirty).toBe(true);
    expect(store().content).toBe("<p>doc 1 v1</p>");
    // Nothing was fetched: Cancel means the switch never happened.
    expect(vi.mocked(getDocument)).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "Patent 2" }));
    await user.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: "Discard" }),
    );

    await waitFor(() => expect(store().documentId).toBe(2));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(store().content).toBe("<p>doc 2 v1</p>");
    expect(store().dirty).toBe(false);
    // The editor cell shows the newly loaded document, not the old one holding a
    // prop it will never read. (This half of the switch survives a constant
    // `key`, because the loading branch unmounts the editor anyway; the path the
    // key alone carries is save-as-new-version, which never sets `loading`.)
    await waitFor(() => expect(screen.getByTestId("editor").textContent).toBe("<p>doc 2 v1</p>"));
  });

  // The dialog's other two outcomes. Both save buttons only start the save; the
  // held-back switch is committed by App's effect when the document stops being
  // dirty. Without this test that whole mechanism — and Task 1 requirement 3 on
  // the switch path — is covered by nothing.
  it("switching while dirty: Save persists in place and then commits the switch", async () => {
    const user = userEvent.setup();
    vi.mocked(listDocuments).mockResolvedValue([
      { id: 1, title: "Patent 1" },
      { id: 2, title: "Patent 2" },
    ]);
    vi.mocked(getDocument).mockImplementation(async (id: number) => detail(id));
    vi.mocked(getVersion).mockImplementation(async (id: number, n: number) => version(id, n));
    vi.mocked(updateVersion).mockResolvedValue(version(1, 1));
    useDocumentStore.setState({ editor: fakeEditor() });

    render(<App />);
    await waitFor(() => expect(store().documentId).toBe(1));
    act(() => store().setDirty(true));

    await user.click(screen.getByRole("button", { name: "Patent 2" }));
    await user.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: "Save" }),
    );

    await waitFor(() => expect(store().documentId).toBe(2));
    expect(updateVersion).toHaveBeenCalledTimes(1);
    expect(updateVersion).toHaveBeenCalledWith(1, 1, "<p>live</p>");
    expect(vi.mocked(createVersion)).not.toHaveBeenCalled(); // requirement 3
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(store().dirty).toBe(false);
  });

  // Task 1 requirement 2, through the control the user actually touches. The
  // store test proves `selectVersion` fetches; this proves the picker is wired to
  // it and that the *editor* — not just the label — ends up on that version.
  it("switching version via the select loads that version", async () => {
    const user = userEvent.setup();
    await renderOpenDocument([1, 2]);
    expect(store().versionNumber).toBe(2);

    await user.selectOptions(screen.getByLabelText("Version"), "1");

    await waitFor(() => expect(store().versionNumber).toBe(1));
    expect(vi.mocked(getVersion)).toHaveBeenCalledWith(1, 1);
    await waitFor(() => expect(screen.getByTestId("editor").textContent).toBe("<p>doc 1 v1</p>"));
    expect((screen.getByLabelText("Version") as HTMLSelectElement).value).toBe("1");
  });

  // Task 1 requirement 1, and the only test that would catch someone dropping
  // `versionNumber` from the editor's `key={docId}:{versionNumber}`: this path
  // never sets `loading`, so nothing else unmounts the editor. Without the key
  // the pane would keep showing the pre-save content while the bar claimed to be
  // on the new version.
  it("save as new version from the bar remounts the editor on the new version", async () => {
    const user = userEvent.setup();
    await renderOpenDocument([1]);
    act(() => store().setDirty(true));
    vi.mocked(createVersion).mockResolvedValue({
      document_id: 1,
      version_number: 2,
      content: "<p>created v2</p>",
      updated_at: "2026-02-02T11:00:00",
    });

    await user.click(screen.getByRole("button", { name: "Save as new version" }));

    await waitFor(() => expect(store().versionNumber).toBe(2));
    expect(createVersion).toHaveBeenCalledWith(1, "<p>live</p>");
    // The server's sanitised echo, reached by remount rather than by a prop write.
    await waitFor(() => expect(screen.getByTestId("editor").textContent).toBe("<p>created v2</p>"));
    expect((screen.getByLabelText("Version") as HTMLSelectElement).value).toBe("2");
    expect(screen.queryByText("Unsaved changes")).toBeNull();
  });

  // The client half of the concurrent-create race the server answers with a 409:
  // a second click while the first write is in flight must not be possible.
  it("disables both save buttons while a save is in flight", async () => {
    const user = userEvent.setup();
    await renderOpenDocument([1]);
    vi.mocked(updateVersion).mockReturnValue(new Promise<VersionRead>(() => {}));

    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(
        true,
      ),
    );
    expect(
      (screen.getByRole("button", { name: "Save as new version" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(createVersion).not.toHaveBeenCalled();
  });

  // A non-array body used to reach `documents.map` and paint a white screen — the
  // one failure mode with nothing at all to read.
  it("shows a message instead of a white screen when the document list is malformed", async () => {
    vi.mocked(listDocuments).mockResolvedValue({ items: [] } as unknown as DocumentSummary[]);

    render(<App />);

    const banner = await screen.findByRole("alert");
    expect(banner.textContent).toContain("The server returned an unreadable list of documents.");
    expect(screen.getByText("No patents to open. Add one on the server, then reload.")).toBeTruthy();
  });

  // Not in the §13 gate, which covers this only in its manual row — but "" is
  // falsy, App guards the editor on `content !== null`, and PLAN §12 calls a
  // truthiness check here the single most likely stress-test failure in the
  // client. A saved-empty version must still open.
  it('mounts the editor for a version whose content is ""', async () => {
    vi.mocked(listDocuments).mockResolvedValue([{ id: 1, title: "Patent 1" }]);
    vi.mocked(getDocument).mockImplementation(async (id: number) => detail(id));
    vi.mocked(getVersion).mockResolvedValue({ ...version(1, 1), content: "" });

    render(<App />);

    await waitFor(() => expect(store().content).toBe(""));
    expect(await screen.findByTestId("editor")).toBeTruthy();
  });
});
