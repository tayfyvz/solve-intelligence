import type { Editor } from "@tiptap/core";
import { getSchema } from "@tiptap/core";
import { DOMParser as PMDOMParser, type Node as PMNode } from "@tiptap/pm/model";
import StarterKit from "@tiptap/starter-kit";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  AiApplyResponse,
  AiChatResponse,
  AiProposal,
  VersionPage,
  VersionRead,
} from "../types";

// One mock for the whole seam; `ApiError`, `toMessage` and `RETRYABLE_STATUSES` stay
// real, because the panel's error copy and its Retry rule are exactly what is under
// test here.
vi.mock("../services/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../services/api")>()),
  aiChat: vi.fn(),
  aiApply: vi.fn(),
  createVersion: vi.fn(),
  getVersion: vi.fn(),
  listVersions: vi.fn(),
  updateVersion: vi.fn(),
  renameVersion: vi.fn(),
}));

const { ApiError, aiApply, aiChat, createVersion, getVersion, listVersions, updateVersion, renameVersion } =
  vi.mocked(await import("../services/api"));
const { useDocumentStore } = await import("../store");
const { highlightKey } = await import("../features/editor/highlight");
const { localFormat } = await import("../features/chat/format");
const { default: ChatPanel } = await import("../features/chat/ChatPanel");
const { default: Message } = await import("../features/chat/Message");

const store = () => useDocumentStore.getState();
const STAMP = "2026-01-01T09:30:00";
const schema = getSchema([StarterKit]);

const docOf = (html: string): PMNode => {
  const body = new window.DOMParser().parseFromString(html, "text/html").body;
  return PMDOMParser.fromSchema(schema).parse(body);
};

/** Two claims, so `claimSpans`' >= 2 rule is satisfied and citations can resolve. */
const CLAIM_DOC = docOf("<p>1. A method.</p><p>2. A device.</p>");

/**
 * Only `editor.test.tsx` and `seedRoundTrip.test.ts` mount real TipTap; testing a
 * 200-line React adapter through jsdom's ProseMirror tests jsdom.
 *
 * `getHTML` is settable per call, because the drift guard compares the value read
 * *before* the await with the value read *after* it — a fake returning a constant
 * would make the most important test in this file unable to fail. And `setContent`
 * honours its second argument exactly as TipTap does: `emitUpdate` is what routes it
 * through `Editor.onUpdate`, the single writer of `dirty`.
 */
function fakeEditor(initial = "<p>Hi</p>") {
  let html = initial;
  const chain = {
    setTextSelection: vi.fn(() => chain),
    setMark: vi.fn(() => chain),
    unsetMark: vi.fn(() => chain),
    run: vi.fn(() => true),
  };
  const editor = {
    chainStub: chain,
    getHTML: () => html,
    setHtml: (next: string) => {
      html = next;
    },
    isDestroyed: false,
    state: { doc: CLAIM_DOC, selection: { from: 0, to: 0, empty: true }, tr: { setMeta: vi.fn() } },
    commands: {
      setContent: vi.fn((next: string, emitUpdate?: boolean) => {
        html = next;
        if (emitUpdate) useDocumentStore.getState().setDirty(true);
      }),
    },
    chain: () => chain,
    view: {
      dispatch: vi.fn(),
      domAtPos: () => ({ node: document.createElement("p") }),
    },
    on: vi.fn(),
    off: vi.fn(),
    registerPlugin: vi.fn(),
    unregisterPlugin: vi.fn(),
  };
  return editor;
}

type FakeEditor = ReturnType<typeof fakeEditor>;

const asEditor = (fake: FakeEditor) => fake as unknown as Editor;

function versionRead(
  n: number,
  content = "<p>applied</p>",
  source: "user" | "ai" = "user",
): VersionRead {
  return {
    document_id: 1,
    version_number: n,
    name: `Version ${n}`,
    content,
    source,
    created_at: STAMP,
    updated_at: STAMP,
  };
}

const versionPage = (numbers: number[], source: "user" | "ai" = "user"): VersionPage => ({
  items: numbers.map((n) => ({
    version_number: n,
    name: `Version ${n}`,
    source,
    created_at: STAMP,
    updated_at: STAMP,
  })),
  total: numbers.length,
  limit: 20,
  offset: 0,
});

const chatResponse = (over: Partial<AiChatResponse> = {}): AiChatResponse => ({
  status: "applied",
  message: "Done.",
  html: "<p>ai</p>",
  proposal: null,
  verification: null,
  warnings: [],
  citations: [],
  options: [],
  ...over,
});

const proposalOf = (over: Partial<AiProposal> = {}): AiProposal => ({
  proposal_id: "p1",
  document_id: 1,
  version_number: 2,
  base_sha256: "abc",
  created_at: STAMP,
  message: "I can delete claim 3.",
  summary: ["Delete claim 3."],
  authors_new_text: false,
  operations: [],
  ...over,
});

const proposalResponse = (over: Partial<AiProposal> = {}) =>
  chatResponse({
    status: "proposal",
    html: null,
    message: "I can delete claim 3.",
    proposal: proposalOf(over),
  });

const applyResponse = (over: Partial<AiApplyResponse> = {}): AiApplyResponse => ({
  status: "applied",
  message: "Deleted claim 3.",
  html: "<p>applied</p>",
  verification: null,
  warnings: [],
  ...over,
});

/** A promise the test resolves by hand, for everything mid-flight. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** Mirrors App.tsx exactly, key included, so version transitions behave as they ship. */
function Harness() {
  const documentId = useDocumentStore((s) => s.documentId);
  const versionNumber = useDocumentStore((s) => s.versionNumber);
  return (
    <ChatPanel key={documentId ?? "none"} documentId={documentId} versionNumber={versionNumber} />
  );
}

function open(editor: FakeEditor, version = 2, origin: "user" | "ai" = "user") {
  useDocumentStore.setState({
    documentId: 1,
    title: "Patent 1",
    versionNumber: version,
    versionName: `Version ${version}`,
    versionSource: "user",
    versionOrigin: origin,
    content: "<p>Hi</p>",
    editor: asEditor(editor),
    dirty: false,
    versions: versionPage([version], origin).items,
    versionsTotal: 1,
  });
}

const user = () => userEvent.setup();

async function ask(text: string) {
  const person = user();
  await person.type(screen.getByLabelText("Ask the AI"), text);
  await person.click(screen.getByRole("button", { name: "Send" }));
}

const applyButton = () => screen.queryByRole("button", { name: "Apply these changes" });

/** The verbatim strings the gate asserts on. Duplicated from the source deliberately:
 *  a test that imported them could not catch the copy changing. */
const DRIFT =
  "You edited the document while the AI was working, so the change was not applied. " +
  "Ask again and it will use your current text.";
const DISCARDED =
  "You saved a new version while the AI was working, so that request was discarded. " +
  "Ask again and it will use the new version.";

beforeEach(() => {
  listVersions.mockResolvedValue(versionPage([1, 2]));
  getVersion.mockResolvedValue(versionRead(1, "<p>v1</p>"));
  // Every createVersion in this file is the confirmProposal path, so the server's echo
  // is always source "ai" unless a test overrides it.
  createVersion.mockResolvedValue(versionRead(3, "<p>applied</p>", "ai"));
  updateVersion.mockResolvedValue(versionRead(2));
  renameVersion.mockResolvedValue({ ...versionRead(2), name: "renamed" });
});

afterEach(() => {
  vi.clearAllMocks();
});

// CP-01. The one-character bug: without the positional `true`, `dirty` stays false
// while the document has changed and the user is told their work is saved.
it("CP-01 an applied edit reaches the editor with emitUpdate = true", async () => {
  const editor = fakeEditor();
  open(editor);
  aiChat.mockResolvedValue(chatResponse({ html: "<p>ai</p>" }));
  render(<Harness />);

  await ask("delete claim 3");

  await waitFor(() => expect(editor.commands.setContent).toHaveBeenCalledWith("<p>ai</p>", true));
  expect(store().dirty).toBe(true);
});

// CP-02. The first document change on a version is confirmed, not applied.
it("CP-02 the first document change on a version prompts", async () => {
  const editor = fakeEditor();
  open(editor);
  aiChat.mockResolvedValue(proposalResponse());
  render(<Harness />);

  await ask("delete claim 3");

  await waitFor(() => expect(applyButton()).not.toBeNull());
  expect(aiChat.mock.calls[0][0].consented).toBe(false);
  expect(editor.commands.setContent).not.toHaveBeenCalled();
  expect(createVersion).not.toHaveBeenCalled();
});

// CP-02, second half: two versions with byte-identical content would slip past the
// server's digest, so the echoed identifiers are cross-checked as well as the store.
it("CP-02 a proposal for another version is not offered", async () => {
  const editor = fakeEditor();
  open(editor, 2);
  aiChat.mockResolvedValue(proposalResponse({ version_number: 7 }));
  render(<Harness />);

  await ask("delete claim 3");

  await waitFor(() => expect(aiChat).toHaveBeenCalled());
  expect(applyButton()).toBeNull();
  expect(screen.queryByText(/I can delete claim 3\./)).toBeNull();
});

// CP-03. Proceed is the whole point of the prompt: it makes the restore point.
it("CP-03 Proceed applies, versions, moves, and grants consent", async () => {
  const editor = fakeEditor();
  open(editor);
  aiChat.mockResolvedValue(proposalResponse());
  aiApply.mockResolvedValue(
    applyResponse({ warnings: ["Claim 4 refers to claim 3, which does not exist."] }),
  );
  render(<Harness />);

  await ask("delete claim 3");
  await waitFor(() => expect(applyButton()).not.toBeNull());
  await user().click(applyButton()!);

  await waitFor(() => expect(store().versionNumber).toBe(3));
  expect(aiApply).toHaveBeenCalledTimes(1);
  // The highest-value output the feature produces, and the thing most likely to be
  // scrolled past in a demo. It is rendered, under its own heading.
  expect(screen.getByText("Warnings")).toBeTruthy();
  expect(screen.getByText("Claim 4 refers to claim 3, which does not exist.")).toBeTruthy();
  expect(editor.commands.setContent).toHaveBeenCalledWith("<p>applied</p>", true);
  expect(createVersion).toHaveBeenCalledTimes(1);
  expect(store().versionSource).toBe("ai");
  expect(store().dirty).toBe(false);
  // The transcript survives the version change it caused.
  expect(screen.getByText("delete claim 3")).toBeTruthy();
  expect(screen.getByText(/Saved as version 3\./)).toBeTruthy();
});

// CP-04. Every change after the first on the same version is ordinary editing —
// and the copy must say so, because nothing was saved.
it("CP-04 a second change on the consented version is silent", async () => {
  const editor = fakeEditor();
  open(editor);
  aiChat.mockResolvedValueOnce(proposalResponse());
  aiApply.mockResolvedValue(applyResponse());
  render(<Harness />);

  await ask("delete claim 3");
  await waitFor(() => expect(applyButton()).not.toBeNull());
  await user().click(applyButton()!);
  await waitFor(() => expect(store().versionNumber).toBe(3));

  createVersion.mockClear();
  aiChat.mockResolvedValueOnce(chatResponse({ message: "Made claim 1 bold.", html: "<p>bold</p>" }));
  await ask("make claim 1 bold");

  await waitFor(() => expect(screen.getByText(/Not saved yet/)).toBeTruthy());
  expect(aiChat.mock.calls[1][0].consented).toBe(true);
  expect(editor.commands.setContent).toHaveBeenCalledWith("<p>bold</p>", true);
  expect(createVersion).not.toHaveBeenCalled();
  expect(applyButton()).toBeNull();
  expect(store().dirty).toBe(true);
});

// CP-05. Declining must cost nothing at all — and must not grant consent.
it("CP-05 Cancel changes nothing", async () => {
  const editor = fakeEditor();
  open(editor);
  aiChat.mockResolvedValue(proposalResponse());
  render(<Harness />);

  await ask("delete claim 3");
  await waitFor(() => expect(applyButton()).not.toBeNull());
  await user().click(screen.getByRole("button", { name: "Cancel" }));

  expect(screen.getByText("Cancelled")).toBeTruthy();
  expect(aiApply).not.toHaveBeenCalled();
  expect(editor.commands.setContent).not.toHaveBeenCalled();
  expect(createVersion).not.toHaveBeenCalled();
  expect(store().dirty).toBe(false);

  await ask("delete claim 4");
  expect(aiChat.mock.calls[1][0].consented).toBe(false);
});

// CP-06. The data-loss case: the edit is in the buffer and nowhere else, so the
// panel must say so and must NOT claim the restore point it failed to create.
it("CP-06 a failed version save leaves it dirty, unsaved and UNCONSENTED", async () => {
  const editor = fakeEditor();
  open(editor);
  aiChat.mockResolvedValue(proposalResponse());
  aiApply.mockResolvedValue(applyResponse());
  createVersion.mockRejectedValue(new ApiError(413, "That version is too large to save."));
  render(<Harness />);

  await ask("delete claim 3");
  await waitFor(() => expect(applyButton()).not.toBeNull());
  await user().click(applyButton()!);

  await waitFor(() => expect(screen.getByText(/could not be saved/)).toBeTruthy());
  expect(editor.commands.setContent).toHaveBeenCalledWith("<p>applied</p>", true);
  expect(store().dirty).toBe(true);
  expect(store().versionSource).toBe("user");
  expect(store().versionNumber).toBe(2);
  expect(screen.getByText("Applied — not saved")).toBeTruthy();
  expect(screen.getByText(/That version is too large to save\./)).toBeTruthy();

  aiChat.mockResolvedValue(proposalResponse());
  await ask("delete claim 4");
  expect(aiChat.mock.calls[1][0].consented).toBe(false);
});

// CP-07. Two `false`s that mean opposite things, told apart by `error`, not by a
// navigation check — a real failure that coincides with navigation must still speak.
describe("CP-07 a superseded version save says nothing", () => {
  it("stays silent when the write was discarded", async () => {
    const editor = fakeEditor();
    open(editor);
    aiChat.mockResolvedValue(proposalResponse());
    aiApply.mockResolvedValue(applyResponse());
    const save = deferred<VersionRead>();
    createVersion.mockReturnValue(save.promise);
    render(<Harness />);

    await ask("delete claim 3");
    await waitFor(() => expect(applyButton()).not.toBeNull());
    await user().click(applyButton()!);
    await waitFor(() => expect(createVersion).toHaveBeenCalled());

    // A selection bumps the store's token, so the save is discarded — `false` with
    // `error === null`.
    await act(async () => {
      await store().selectVersion(1);
      save.resolve(versionRead(3));
    });

    await waitFor(() => expect(store().versionNumber).toBe(1));
    expect(screen.queryByText(/could not be saved/)).toBeNull();
    expect(store().error).toBeNull();
  });

  // The discriminator is `error`, not the version number: a genuine failure must
  // never be swallowed as "the user moved on".
  //
  // The companion case — a genuine failure *while* the user navigates — cannot be written,
  // and that is a fact about the shipped store: its catch returns on `!isCurrent()` before
  // writing `error`, so such a rejection is silent by construction.
  it("speaks when the save genuinely failed", async () => {
    const editor = fakeEditor();
    open(editor);
    aiChat.mockResolvedValue(proposalResponse());
    aiApply.mockResolvedValue(applyResponse());
    createVersion.mockRejectedValue(new ApiError(500, "The server broke."));
    render(<Harness />);

    await ask("delete claim 3");
    await waitFor(() => expect(applyButton()).not.toBeNull());
    await user().click(applyButton()!);

    await waitFor(() => expect(screen.getByText(/could not be saved/)).toBeTruthy());
    expect(screen.getByText(/The server broke\./)).toBeTruthy();
    expect(screen.getByText("Applied — not saved")).toBeTruthy();
  });
});

// CP-08. `sending` is React state and would not stop the second click in the same
// frame; the ref does.
it("CP-08 double-clicking Apply sends exactly one apply and creates one version", async () => {
  const editor = fakeEditor();
  open(editor);
  aiChat.mockResolvedValue(proposalResponse());
  aiApply.mockResolvedValue(applyResponse());
  render(<Harness />);

  await ask("delete claim 3");
  await waitFor(() => expect(applyButton()).not.toBeNull());

  const button = applyButton()!;
  await act(async () => {
    button.click();
    button.click();
  });

  await waitFor(() => expect(store().versionNumber).toBe(3));
  expect(aiApply).toHaveBeenCalledTimes(1);
  expect(createVersion).toHaveBeenCalledTimes(1);
});

// CP-09. Applying HTML computed for version 3 into version 1's editor is the worst
// outcome in the whole feature.
it("CP-09 switching version mid-apply does not touch the new version", async () => {
  const editor = fakeEditor();
  open(editor);
  aiChat.mockResolvedValue(proposalResponse());
  const apply = deferred<AiApplyResponse>();
  aiApply.mockReturnValue(apply.promise);
  render(<Harness />);

  await ask("delete claim 3");
  await waitFor(() => expect(applyButton()).not.toBeNull());
  await user().click(applyButton()!);
  await waitFor(() => expect(aiApply).toHaveBeenCalled());

  await act(async () => {
    await store().selectVersion(1);
    apply.resolve(applyResponse());
  });

  await waitFor(() => expect(store().versionNumber).toBe(1));
  expect(editor.commands.setContent).not.toHaveBeenCalled();
  expect(createVersion).not.toHaveBeenCalled();
});

// CP-10. A 409 means the document moved: the honest affordance is to ask again,
// which is why no Retry is offered.
it("CP-10 a stale proposal 409 fails closed", async () => {
  const editor = fakeEditor();
  open(editor);
  aiChat.mockResolvedValue(proposalResponse());
  aiApply.mockRejectedValue(
    new ApiError(409, "The document changed after this suggestion was written."),
  );
  render(<Harness />);

  await ask("delete claim 3");
  await waitFor(() => expect(applyButton()).not.toBeNull());
  await user().click(applyButton()!);

  await waitFor(() => expect(screen.getByText(/changed after this suggestion/)).toBeTruthy());
  expect(screen.getByText("Not applied")).toBeTruthy();
  expect(editor.commands.setContent).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();

  aiChat.mockResolvedValue(proposalResponse());
  await ask("delete claim 4");
  expect(aiChat.mock.calls[1][0].consented).toBe(false);
});

// CP-11. A user-driven version switch is a different document on screen: the
// conversation about the old one goes with it.
it("CP-11 switching version clears consent and the transcript", async () => {
  const editor = fakeEditor();
  open(editor);
  aiChat.mockResolvedValue(proposalResponse());
  aiApply.mockResolvedValue(applyResponse());
  render(<Harness />);

  await ask("delete claim 3");
  await waitFor(() => expect(applyButton()).not.toBeNull());
  await user().click(applyButton()!);
  await waitFor(() => expect(store().versionNumber).toBe(3));

  await act(async () => {
    await store().selectVersion(1);
  });

  expect(screen.queryByText("delete claim 3")).toBeNull();
  aiChat.mockResolvedValue(proposalResponse({ version_number: 1 }));
  await ask("delete claim 4");
  expect(aiChat.mock.calls[1][0].consented).toBe(false);
});

// CP-12. The `versionSource === "ai"` branch: the transcript survives the version
// change the panel itself caused.
it("CP-12 an AI-created version keeps the transcript", async () => {
  const editor = fakeEditor();
  open(editor);
  aiChat.mockResolvedValue(proposalResponse());
  aiApply.mockResolvedValue(applyResponse());
  render(<Harness />);

  await ask("delete claim 3");
  await waitFor(() => expect(applyButton()).not.toBeNull());
  await user().click(applyButton()!);

  await waitFor(() => expect(store().versionNumber).toBe(3));
  expect(screen.getByText("delete claim 3")).toBeTruthy();
  expect(screen.getByText(/I can delete claim 3\./)).toBeTruthy();
});

// CP-13 (CT10). `PUT` never moves `versionNumber`, and consent is keyed on it — so
// this falls out of the design rather than out of a special case.
it("CP-13 an in-place Save keeps consent", async () => {
  const editor = fakeEditor();
  open(editor);
  aiChat.mockResolvedValue(proposalResponse());
  aiApply.mockResolvedValue(applyResponse());
  render(<Harness />);

  await ask("delete claim 3");
  await waitFor(() => expect(applyButton()).not.toBeNull());
  await user().click(applyButton()!);
  await waitFor(() => expect(store().versionNumber).toBe(3));

  await act(async () => {
    await store().save();
  });

  expect(store().dirty).toBe(false);
  expect(store().versionNumber).toBe(3);
  aiChat.mockResolvedValue(chatResponse());
  await ask("make claim 1 bold");
  expect(aiChat.mock.calls[1][0].consented).toBe(true);
});

// CP-14. Without this on the proposal path, keystrokes typed during /apply are
// silently destroyed and a version is created containing text the user never saw.
describe("CP-14 the drift guard refuses on BOTH paths", () => {
  it("consented path", async () => {
    const editor = fakeEditor();
    open(editor);
    aiChat.mockResolvedValueOnce(proposalResponse());
    aiApply.mockResolvedValue(applyResponse());
    render(<Harness />);

    await ask("delete claim 3");
    await waitFor(() => expect(applyButton()).not.toBeNull());
    await user().click(applyButton()!);
    await waitFor(() => expect(store().versionNumber).toBe(3));

    const chat = deferred<AiChatResponse>();
    aiChat.mockReturnValue(chat.promise);
    editor.commands.setContent.mockClear();
    await ask("make claim 1 bold");
    await act(async () => {
      // The user types while the AI is working.
      editor.setHtml("<p>typed</p>");
      chat.resolve(chatResponse({ html: "<p>ai</p>" }));
    });

    await waitFor(() => expect(screen.getByText(DRIFT)).toBeTruthy());
    expect(editor.commands.setContent).not.toHaveBeenCalled();
  });

  it("proposal path", async () => {
    const editor = fakeEditor();
    open(editor);
    aiChat.mockResolvedValue(proposalResponse());
    const apply = deferred<AiApplyResponse>();
    aiApply.mockReturnValue(apply.promise);
    render(<Harness />);

    await ask("delete claim 3");
    await waitFor(() => expect(applyButton()).not.toBeNull());
    await user().click(applyButton()!);
    await waitFor(() => expect(aiApply).toHaveBeenCalled());

    await act(async () => {
      editor.setHtml("<p>typed</p>");
      apply.resolve(applyResponse());
    });

    await waitFor(() => expect(screen.getByText(DRIFT)).toBeTruthy());
    expect(editor.commands.setContent).not.toHaveBeenCalled();
    expect(createVersion).not.toHaveBeenCalled();
    expect(screen.getByText("Not applied")).toBeTruthy();

    aiChat.mockResolvedValue(proposalResponse());
    await ask("delete claim 4");
    expect(aiChat.mock.calls[1][0].consented).toBe(false);
  });
});

// CP-15. The live buffer, not the stored content: it keeps the digest consistent
// AND it is the only reason an unsaved manual edit survives an AI edit.
it("CP-15 the live buffer is what gets sent", async () => {
  const editor = fakeEditor();
  open(editor);
  useDocumentStore.setState({ content: "<p>stored, and stale</p>" });
  aiChat.mockResolvedValue(proposalResponse());
  aiApply.mockResolvedValue(applyResponse());
  render(<Harness />);

  editor.setHtml("<p>typed by hand</p>");
  await ask("delete claim 3");
  await waitFor(() => expect(applyButton()).not.toBeNull());
  expect(aiChat.mock.calls[0][0].html).toBe("<p>typed by hand</p>");

  // Typed again between the proposal and the click: /apply gets what is on screen NOW.
  editor.setHtml("<p>typed again</p>");
  await user().click(applyButton()!);

  await waitFor(() => expect(aiApply).toHaveBeenCalled());
  expect(aiApply.mock.calls[0][0].html).toBe("<p>typed again</p>");
});

// CP-16. Two Proceed buttons on screen is an ambiguity the user should not have to
// resolve — and the older one would 409.
it("CP-16 a new send supersedes an open proposal", async () => {
  const editor = fakeEditor();
  open(editor);
  aiChat.mockResolvedValue(proposalResponse());
  render(<Harness />);

  await ask("delete claim 3");
  await waitFor(() => expect(applyButton()).not.toBeNull());
  await ask("delete claim 4");

  await waitFor(() => expect(screen.getByText(/Superseded/)).toBeTruthy());
  expect(screen.getAllByRole("button", { name: "Apply these changes" })).toHaveLength(1);
});

// CP-17. Asking the same thing twice in one patent must not fail the second save for
// a reason that has nothing to do with the edit.
it("CP-17 a duplicate version name falls back to the default", async () => {
  const editor = fakeEditor();
  open(editor);
  aiChat.mockResolvedValue(proposalResponse());
  aiApply.mockResolvedValue(applyResponse());
  createVersion
    .mockRejectedValueOnce(new ApiError(409, 'A version called "AI: delete claim 3" already exists.'))
    .mockResolvedValueOnce(versionRead(3, "<p>applied</p>", "ai"));
  render(<Harness />);

  await ask("delete claim 3");
  await waitFor(() => expect(applyButton()).not.toBeNull());
  await user().click(applyButton()!);

  await waitFor(() => expect(store().versionNumber).toBe(3));
  expect(createVersion).toHaveBeenCalledTimes(2);
  expect(createVersion.mock.calls[0][2]).toBe("AI: delete claim 3");
  expect(createVersion.mock.calls[1][2]).toBeNull();
  expect(screen.queryByText(/could not be saved/)).toBeNull();
  expect(screen.getByText(/Saved as version 3\./)).toBeTruthy();
});

// CP-18 (CT12). A rename moves no key field, so nothing about consent may move.
it("CP-18 renaming the open version does not disturb consent", async () => {
  const editor = fakeEditor();
  open(editor);
  aiChat.mockResolvedValue(proposalResponse());
  aiApply.mockResolvedValue(applyResponse());
  render(<Harness />);

  await ask("delete claim 3");
  await waitFor(() => expect(applyButton()).not.toBeNull());
  await user().click(applyButton()!);
  await waitFor(() => expect(store().versionNumber).toBe(3));

  renameVersion.mockResolvedValue({ ...versionRead(3), name: "Renamed" });
  await act(async () => {
    await store().renameVersion(3, "Renamed");
  });

  expect(store().versionNumber).toBe(3);
  expect(screen.getByText("delete claim 3")).toBeTruthy();
  aiChat.mockResolvedValue(chatResponse());
  await ask("make claim 1 bold");
  expect(aiChat.mock.calls[1][0].consented).toBe(true);
});

// CP-19. Consent now follows the version's persisted `source`, not local React
// state, so it survives a ChatPanel remount (a reload, a route change) as long as
// the open version's stored origin is still "ai". This is the behavior the store's
// `versionOrigin` field exists for.
it("CP-19 consent survives a remount, tracking the version's persisted origin", async () => {
  const editor = fakeEditor();
  open(editor);
  aiChat.mockResolvedValue(proposalResponse());
  aiApply.mockResolvedValue(applyResponse());
  const view = render(<Harness />);

  await ask("delete claim 3");
  await waitFor(() => expect(applyButton()).not.toBeNull());
  await user().click(applyButton()!);
  await waitFor(() => expect(store().versionNumber).toBe(3));
  expect(store().versionOrigin).toBe("ai");

  view.unmount();
  aiChat.mockResolvedValue(chatResponse());
  render(<Harness />);
  await ask("make claim 1 bold");

  expect(aiChat.mock.calls[1][0].consented).toBe(true);
});

// CP-20. The worked acceptance scenario, end to end.
it("CP-20 the worked acceptance scenario", async () => {
  const editor = fakeEditor();
  open(editor, 2);
  render(<Harness />);

  // 1. Unconsented, document-changing → a proposal, nothing written.
  aiChat.mockResolvedValueOnce(proposalResponse({ message: "I can add a dependent claim." }));
  await ask("add a dependent claim after claim 2");
  await waitFor(() => expect(applyButton()).not.toBeNull());
  expect(aiChat.mock.calls[0][0].consented).toBe(false);
  expect(editor.commands.setContent).not.toHaveBeenCalled();
  expect(store().dirty).toBe(false);

  // 2-3. Apply → version 3, consent moves to it, transcript survives.
  aiApply.mockResolvedValue(applyResponse({ message: "Added a dependent claim." }));
  await user().click(applyButton()!);
  await waitFor(() => expect(store().versionNumber).toBe(3));
  expect(store().versionSource).toBe("ai");
  expect(store().dirty).toBe(false);
  expect(screen.getByText(/Saved as version 3\./)).toBeTruthy();

  // 4. Consented → applied straight in, no version, and the copy says "not saved".
  createVersion.mockClear();
  aiChat.mockResolvedValueOnce(chatResponse({ message: "Made claim 1 bold.", html: "<p>bold</p>" }));
  await ask("make claim 1 bold");
  await waitFor(() => expect(screen.getByText(/Applied to version 3\. Not saved yet/)).toBeTruthy());
  expect(aiChat.mock.calls[1][0].consented).toBe(true);
  expect(createVersion).not.toHaveBeenCalled();
  expect(store().versionNumber).toBe(3);
  expect(store().dirty).toBe(true);
  expect(applyButton()).toBeNull();

  // 5. The user switches to version 1 — transcript and consent go.
  await act(async () => {
    await store().selectVersion(1);
  });
  expect(store().versionNumber).toBe(1);
  expect(store().versionSource).toBe("user");
  expect(store().dirty).toBe(false);
  expect(screen.queryByText("make claim 1 bold")).toBeNull();

  // 6-7. Prompted again; Proceed puts the new version on the END.
  aiChat.mockResolvedValueOnce(proposalResponse({ version_number: 1 }));
  await ask("delete claim 3");
  await waitFor(() => expect(applyButton()).not.toBeNull());
  expect(aiChat.mock.calls[2][0].consented).toBe(false);

  createVersion.mockResolvedValue(versionRead(4, "<p>applied</p>", "ai"));
  await user().click(applyButton()!);
  await waitFor(() => expect(store().versionNumber).toBe(4));
  expect(screen.getByText(/Saved as version 4\./)).toBeTruthy();
});

// CP-21. Retrying a 413 cannot succeed and offering it would be theatre.
describe("CP-21 a transient failure offers Retry; a permanent one does not", () => {
  it.each([429, 502, 504])("%d offers Retry", async (status) => {
    const editor = fakeEditor();
    open(editor);
    aiChat.mockRejectedValueOnce(new ApiError(status, "The AI service is busy right now."));
    render(<Harness />);

    await ask("delete claim 3");
    await waitFor(() => expect(screen.getByText(/busy right now/)).toBeTruthy());

    aiChat.mockResolvedValueOnce(proposalResponse());
    editor.setHtml("<p>changed since</p>");
    await user().click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(aiChat).toHaveBeenCalledTimes(2));
    const retried = aiChat.mock.calls[1][0];
    expect(retried.instruction).toBe("delete claim 3");
    // Freshly captured, not replayed: the retry uses the document as it is now.
    expect(retried.html).toBe("<p>changed since</p>");
    expect(retried.consented).toBe(false);
  });

  it.each([413, 422])("%d does not", async (status) => {
    open(fakeEditor());
    aiChat.mockRejectedValue(new ApiError(status, "That document is too large."));
    render(<Harness />);

    await ask("delete claim 3");

    await waitFor(() => expect(screen.getByText(/too large/)).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("503 is sticky, keeps the composer enabled, and offers no Retry", async () => {
    open(fakeEditor());
    aiChat.mockRejectedValue(new ApiError(503, "AI is not configured."));
    render(<Harness />);

    await ask("delete claim 3");

    await waitFor(() => expect(screen.getByText(/AI is unavailable in this environment/)).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(screen.getByLabelText("Ask the AI")).not.toHaveProperty("disabled", true);
  });

  it("renders Retry on the last message only", async () => {
    const editor = fakeEditor();
    open(editor);
    aiChat.mockRejectedValueOnce(new ApiError(429, "The AI service is busy right now."));
    render(<Harness />);
    await ask("delete claim 3");
    await waitFor(() => expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy());

    aiChat.mockResolvedValueOnce(chatResponse({ status: "answer", html: null, message: "Claim 3 covers X." }));
    await ask("what does claim 3 cover?");

    await waitFor(() => expect(screen.getByText("Claim 3 covers X.")).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });
});

// CP-22. A meta-only transaction cannot mark the document dirty — the entire reason
// a decoration plugin is allowed in a codebase with a one-writer dirty rule.
it("CP-22 a citation chip scrolls and paints without stealing focus", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const scroll = vi.spyOn(Element.prototype, "scrollIntoView");
  try {
    const editor = fakeEditor();
    open(editor);
    aiChat.mockResolvedValue(
      chatResponse({ status: "answer", html: null, message: "Claim 1 covers a method.", citations: [1] }),
    );
    render(<Harness />);

    await ask("what does claim 1 cover?");
    const chip = await screen.findByRole("button", { name: "Show claim 1" });

    const composer = screen.getByLabelText("Ask the AI");
    (composer as HTMLTextAreaElement).focus();
    await user().click(chip);

    expect(scroll).toHaveBeenCalledTimes(1);
    expect(editor.view.dispatch).toHaveBeenCalledTimes(1);
    expect(editor.state.tr.setMeta).toHaveBeenCalledWith(
      highlightKey,
      expect.objectContaining({ kind: "citation" }),
    );
    expect(document.activeElement).toBe(composer);
    expect(editor.commands.setContent).not.toHaveBeenCalled();
    expect(store().dirty).toBe(false);

    await act(async () => {
      vi.advanceTimersByTime(2_500);
    });
    expect(editor.state.tr.setMeta).toHaveBeenCalledWith(highlightKey, { kind: "clear" });
  } finally {
    scroll.mockRestore();
    vi.useRealTimers();
  }
});

// CP-23. A stale question's options must not be clickable three turns later.
it("CP-23 options render on the last assistant message only", async () => {
  open(fakeEditor());
  aiChat.mockResolvedValueOnce(
    chatResponse({
      status: "needs_clarification",
      html: null,
      message: "Which claim did you mean?",
      options: ["Make claim 1 bold."],
    }),
  );
  render(<Harness />);
  await ask("make it bold");
  await waitFor(() => expect(screen.getByRole("button", { name: "Make claim 1 bold." })).toBeTruthy());

  aiChat.mockResolvedValueOnce(
    chatResponse({
      status: "needs_clarification",
      html: null,
      message: "Which one?",
      options: ["Make claim 2 bold."],
    }),
  );
  await ask("no, the other one");

  await waitFor(() => expect(screen.getByRole("button", { name: "Make claim 2 bold." })).toBeTruthy());
  expect(screen.queryByRole("button", { name: "Make claim 1 bold." })).toBeNull();

  aiChat.mockResolvedValueOnce(chatResponse({ status: "answer", html: null, message: "Claim 2 covers Y." }));
  await ask("what does claim 2 cover?");

  await waitFor(() => expect(screen.getByText("Claim 2 covers Y.")).toBeTruthy());
  expect(screen.queryByRole("button", { name: "Make claim 2 bold." })).toBeNull();

  // …and the loop cannot ratchet: a non-clarification answer resets both scalars,
  // so the NEXT ambiguous instruction gets a fresh budget of two questions.
  aiChat.mockResolvedValueOnce(chatResponse({ status: "answer", html: null, message: "Claim 1 covers X." }));
  await ask("what does claim 1 cover?");

  await waitFor(() => expect(aiChat).toHaveBeenCalledTimes(4));
  expect(aiChat.mock.calls[3][0].pending_question).toBeNull();
  expect(aiChat.mock.calls[3][0].clarify_count).toBe(0);
});

// CP-24. Yanking the view away from a warning the user is reading is worse than a
// missed scroll.
describe("CP-24 auto-scroll respects the 80 px rule", () => {
  const measure = (element: HTMLElement, scrollTop: number) => {
    Object.defineProperty(element, "scrollHeight", { value: 1_000, configurable: true });
    Object.defineProperty(element, "clientHeight", { value: 400, configurable: true });
    element.scrollTop = scrollTop;
  };

  const cases: [string, number, boolean][] = [
    // scrollHeight - scrollTop - clientHeight: 0 px, 300 px, exactly 80 px from the bottom.
    ["at the bottom", 600, true],
    ["scrolled up 300 px", 300, false],
    ["exactly 80 px from the bottom", 520, true],
  ];

  it.each(cases)("%s", async (_label, scrollTop, expectedToScroll) => {
    open(fakeEditor());
    aiChat.mockResolvedValue(chatResponse({ status: "answer", html: null, message: "An answer." }));
    render(<Harness />);
    await ask("what does claim 1 cover?");
    await waitFor(() => expect(screen.getByText("An answer.")).toBeTruthy());

    const list = screen.getByTestId("message-list");
    measure(list, scrollTop);
    await act(async () => {
      list.dispatchEvent(new Event("scroll", { bubbles: true }));
    });

    aiChat.mockResolvedValue(chatResponse({ status: "answer", html: null, message: "Another answer." }));
    await ask("and claim 2?");
    await waitFor(() => expect(screen.getByText("Another answer.")).toBeTruthy());

    expect(list.scrollTop).toBe(expectedToScroll ? 1_000 : scrollTop);
  });
});

// CP-25. Ignoring applyHtml's return value put a success claim directly beneath an
// error, with the document unchanged.
describe("CP-25 a setContent throw produces one error and no success claim", () => {
  const THROWN = "The AI returned content the editor could not apply. The document was not changed.";

  it("consented path", async () => {
    const editor = fakeEditor();
    open(editor);
    aiChat.mockResolvedValueOnce(proposalResponse());
    aiApply.mockResolvedValue(applyResponse());
    render(<Harness />);
    await ask("delete claim 3");
    await waitFor(() => expect(applyButton()).not.toBeNull());
    await user().click(applyButton()!);
    await waitFor(() => expect(store().versionNumber).toBe(3));

    editor.commands.setContent.mockImplementation(() => {
      throw new Error("invalid content");
    });
    useDocumentStore.setState({ dirty: false });
    aiChat.mockResolvedValueOnce(chatResponse({ html: "<p>bad</p>" }));
    await ask("make claim 1 bold");

    await waitFor(() => expect(screen.getByText(THROWN)).toBeTruthy());
    expect(screen.queryByText(/Applied to version/)).toBeNull();
    expect(screen.queryByText(/Not saved yet/)).toBeNull();
    expect(store().dirty).toBe(false);
  });

  it("proposal path", async () => {
    const editor = fakeEditor();
    open(editor);
    editor.commands.setContent.mockImplementation(() => {
      throw new Error("invalid content");
    });
    aiChat.mockResolvedValue(proposalResponse());
    aiApply.mockResolvedValue(applyResponse());
    render(<Harness />);

    await ask("delete claim 3");
    await waitFor(() => expect(applyButton()).not.toBeNull());
    await user().click(applyButton()!);

    await waitFor(() => expect(screen.getByText(THROWN)).toBeTruthy());
    expect(screen.getByText("Not applied")).toBeTruthy();
    expect(createVersion).not.toHaveBeenCalled();
    expect(screen.queryByText(/Saved as version/)).toBeNull();
  });
});

/** Holds a selection the way the editor's `selectionUpdate` would. */
async function holdSelection(editor: FakeEditor) {
  const handler = editor.on.mock.calls.find(([event]) => event === "selectionUpdate")?.[1] as
    | ((payload: { editor: Editor }) => void)
    | undefined;
  editor.state.selection = { from: 1, to: 8, empty: false };
  await act(async () => {
    handler?.({ editor: asEditor(editor) });
  });
}

// CP-26. ~30 lines of regex that mutate the document with no round-trip and no
// consent gate — tested, not assumed.
describe("CP-26 the format fast-path never leaves the browser", () => {
  const marks: [string, string][] = [
    ["Make the selected text italic", "italic"],
    ["make this bold", "bold"],
    ["Please make the selection strikethrough.", "strike"],
    ["can you make that bold?", "bold"],
    ["turn it into italics", "italic"],
  ];

  it.each(marks)("%s applies %s locally", async (instruction, mark) => {
    const editor = fakeEditor();
    open(editor);
    render(<Harness />);
    await holdSelection(editor);

    await ask(instruction);

    expect(editor.chainStub.setTextSelection).toHaveBeenCalledWith({ from: 1, to: 8 });
    expect(editor.chainStub.setMark).toHaveBeenCalledWith(mark);
    expect(aiChat).not.toHaveBeenCalled();
    expect(createVersion).not.toHaveBeenCalled();
    expect(screen.getByText(/no AI call/)).toBeTruthy();
    expect((screen.getByLabelText("Ask the AI") as HTMLTextAreaElement).value).toBe("");
  });

  it.each([
    ["remove the bold from the selection", "bold"],
    ["clear italics", "italic"],
  ])("%s removes %s locally", async (instruction, mark) => {
    const editor = fakeEditor();
    open(editor);
    render(<Harness />);
    await holdSelection(editor);

    await ask(instruction);

    expect(editor.chainStub.unsetMark).toHaveBeenCalledWith(mark);
    expect(aiChat).not.toHaveBeenCalled();
  });

  it.each(["Make claim 3 italic", "rewrite this", "un-bold this", "delete claim 3"])(
    "%s falls through to the server",
    async (instruction) => {
      const editor = fakeEditor();
      open(editor);
      aiChat.mockResolvedValue(proposalResponse());
      render(<Harness />);
      await holdSelection(editor);

      await ask(instruction);

      await waitFor(() => expect(aiChat).toHaveBeenCalledTimes(1));
      expect(editor.chainStub.setMark).not.toHaveBeenCalled();
    },
  );

  it("does nothing locally with no selection held", async () => {
    const editor = fakeEditor();
    open(editor);
    aiChat.mockResolvedValue(chatResponse());
    render(<Harness />);

    await ask("make this bold");

    await waitFor(() => expect(aiChat).toHaveBeenCalledTimes(1));
    expect(editor.chainStub.setMark).not.toHaveBeenCalled();
  });
});

// CP-27. Without supersede above the fast-path, the stale Apply button stays on
// screen and 409s — the exact bug supersede exists to prevent.
it("CP-27 the fast-path supersedes an open proposal", async () => {
  const editor = fakeEditor();
  open(editor);
  aiChat.mockResolvedValue(proposalResponse());
  render(<Harness />);

  await ask("delete claim 3");
  await waitFor(() => expect(applyButton()).not.toBeNull());
  await holdSelection(editor);

  await ask("make this bold");

  expect(editor.chainStub.setMark).toHaveBeenCalledWith("bold");
  expect(screen.getByText(/Superseded/)).toBeTruthy();
  expect(applyButton()).toBeNull();
});

// CP-28. The user waited 20 s: they get a sentence, not an empty panel.
describe("CP-28 saving a new version during an AI call leaves a sentence, not silence", () => {
  it("says what happened when the version moved", async () => {
    const editor = fakeEditor();
    open(editor);
    const chat = deferred<AiChatResponse>();
    aiChat.mockReturnValue(chat.promise);
    render(<Harness />);

    await ask("delete claim 3");
    // The save lands first — seconds before the AI answers, which is the whole
    // point: the version moves while the request is still in flight.
    await act(async () => {
      await store().saveAsNewVersion();
    });
    await waitFor(() => expect(screen.getByText(DISCARDED)).toBeTruthy());

    await act(async () => {
      chat.resolve(chatResponse({ html: "<p>ai</p>" }));
    });

    expect(editor.commands.setContent).not.toHaveBeenCalled();
    expect(screen.queryByText("delete claim 3")).toBeNull();
    expect(store().dirty).toBe(false);
    expect(store().versionSource).toBe("user");
  });

  it("an in-place Save keeps the transcript and the response lands", async () => {
    const editor = fakeEditor();
    open(editor);
    const chat = deferred<AiChatResponse>();
    aiChat.mockReturnValue(chat.promise);
    render(<Harness />);

    await ask("delete claim 3");
    await act(async () => {
      await store().save();
      chat.resolve(chatResponse({ html: "<p>ai</p>" }));
    });

    await waitFor(() => expect(editor.commands.setContent).toHaveBeenCalledWith("<p>ai</p>", true));
    expect(screen.getByText("delete claim 3")).toBeTruthy();
    expect(screen.queryByText(DISCARDED)).toBeNull();
  });
});

// The pure half of the format fast-path: every pattern `localFormat` claims to match.
describe("CP-29 localFormat matches exactly the documented table", () => {
  const table: [string, { mark: string; on: boolean } | null][] = [
    ["Make the selected text italic", { mark: "italic", on: true }],
    ["make this bold", { mark: "bold", on: true }],
    ["Make selected text bold", { mark: "bold", on: true }],
    ["Please make the selection strikethrough.", { mark: "strike", on: true }],
    ["turn it into italics", { mark: "italic", on: true }],
    ["can you make that bold?", { mark: "bold", on: true }],
    ["remove the bold from the selection", { mark: "bold", on: false }],
    ["clear italics", { mark: "italic", on: false }],
    // The documented accepted false negative: widening the pattern to catch it
    // starts catching things it should not.
    ["un-bold this", null],
    // The guard clause: a false positive here silently formats the wrong thing.
    ["Make claim 3 italic", null],
    ["make every claim bold", null],
    ["bold the whole document", null],
    ["rewrite this", null],
    ["make it shorter", null],
    ["delete claim 3", null],
  ];

  it.each(table)("%s", (instruction, expected) => {
    expect(localFormat(instruction)).toEqual(expected);
  });
});

// CP-30. The three assertions CP-26's table does not carry.
describe("CP-30 the fast-path's three negatives", () => {
  it("is idempotent: setMark twice, never unsetMark", async () => {
    const editor = fakeEditor();
    open(editor);
    render(<Harness />);
    await holdSelection(editor);

    await ask("make the selected text italic");
    await ask("make the selected text italic");

    expect(editor.chainStub.setMark).toHaveBeenCalledTimes(2);
    expect(editor.chainStub.setMark).toHaveBeenCalledWith("italic");
    expect(editor.chainStub.unsetMark).not.toHaveBeenCalled();
  });

  it("is not gated by consent (CT14)", async () => {
    const editor = fakeEditor();
    open(editor);
    render(<Harness />);
    await holdSelection(editor);

    // No proposal has ever been accepted on this version: consent is null.
    await ask("make the selected text italic");

    expect(editor.chainStub.setMark).toHaveBeenCalledWith("italic");
    expect(aiChat).not.toHaveBeenCalled();
    expect(createVersion).not.toHaveBeenCalled();
    expect(store().versionNumber).toBe(2);
  });
});

// Clarification options are complete instructions rather than {id, label}, precisely so
// this is testable without server state.
it("CP-31 clarification options send verbatim and disable while sending", async () => {
  const options = ["Make claim 1 bold.", "Make claim 2 bold.", "Make claim 3 bold."];
  const editor = fakeEditor();
  open(editor);
  aiChat.mockResolvedValueOnce(
    chatResponse({
      status: "needs_clarification",
      html: null,
      message: "Which claim did you mean?",
      options,
    }),
  );
  render(<Harness />);

  await ask("make it bold");
  await waitFor(() => expect(screen.getByText("Which claim did you mean?")).toBeTruthy());

  const list = screen.getByTestId("message-list");
  const buttons = within(list)
    .getAllByRole("button")
    .filter((button) => options.includes(button.textContent ?? ""));
  expect(buttons.map((button) => button.textContent)).toEqual(options);
  expect(editor.commands.setContent).not.toHaveBeenCalled();
  expect(createVersion).not.toHaveBeenCalled();
  expect(store().dirty).toBe(false);

  const pending = deferred<AiChatResponse>();
  aiChat.mockReturnValue(pending.promise);
  await user().click(buttons[2]);

  await waitFor(() => expect(aiChat).toHaveBeenCalledTimes(2));
  const second = aiChat.mock.calls[1][0];
  expect(second.instruction).toBe(options[2]);
  expect(second.consented).toBe(false);
  expect(second.pending_question).toBe("Which claim did you mean?");
  expect(second.clarify_count).toBe(1);

  // Sending appends the user's own bubble, so the option bubble is no longer last and its
  // buttons are gone entirely — stricter than disabled, which is why the
  // disabled-while-sending half is asserted against `Message` directly below.
  expect(screen.queryByRole("button", { name: options[2] })).toBeNull();

  await act(async () => {
    pending.resolve(chatResponse({ status: "no_change", html: null, message: "Nothing to do." }));
  });
});

// CP-32. Confirmed live: four clicks on Send inside one frame fired four POSTs.
// React re-renders the disabled button too late, so all four read `sending ===
// false`; the ref is what stops them. The drift guard saved correctness, at the
// cost of three LLM calls and three confusing "you edited the document" bubbles.
it("CP-32 four rapid clicks on Send fire exactly one request", async () => {
  const editor = fakeEditor();
  open(editor);
  const chat = deferred<AiChatResponse>();
  aiChat.mockReturnValue(chat.promise);
  render(<Harness />);

  await user().type(screen.getByLabelText("Ask the AI"), "delete claim 3");
  const send = screen.getByRole("button", { name: "Send" });
  await act(async () => {
    send.click();
    send.click();
    send.click();
    send.click();
  });

  expect(aiChat).toHaveBeenCalledTimes(1);
  // One request means one question in the transcript, too.
  expect(screen.getAllByText("delete claim 3")).toHaveLength(1);

  await act(async () => {
    chat.resolve(chatResponse({ status: "answer", html: null, message: "Claim 3 covers X." }));
  });
  expect(screen.queryByText(DRIFT)).toBeNull();
});

// CP-32b. The store's drift guard on the AI's own version save: the user types
// during the POST, so the store keeps them where they are rather than remounting
// the editor on the server's echo. The panel must not then claim they moved.
it("CP-32b typing during the AI's version save keeps the text and says so", async () => {
  const editor = fakeEditor();
  open(editor);
  aiChat.mockResolvedValue(proposalResponse());
  aiApply.mockResolvedValue(applyResponse());
  const save = deferred<VersionRead>();
  createVersion.mockReturnValue(save.promise);
  render(<Harness />);

  await ask("delete claim 3");
  await waitFor(() => expect(applyButton()).not.toBeNull());
  await user().click(applyButton()!);
  await waitFor(() => expect(createVersion).toHaveBeenCalled());

  await act(async () => {
    editor.setHtml("<p>applied and then typed</p>"); // the user types mid-save
    save.resolve(versionRead(3, "<p>applied</p>", "ai"));
  });

  await waitFor(() => expect(screen.getByText(/still on version 2/)).toBeTruthy());
  // The version was created — but we stayed put, so the keystrokes are intact.
  expect(store().versionNumber).toBe(2);
  expect(store().dirty).toBe(true);
  expect(editor.getHTML()).toBe("<p>applied and then typed</p>");
  expect(screen.queryByText(/Saved as version 3\./)).toBeNull();
});

// CP-33. Both of these guards used to return in silence: the user clicked "Apply
// these changes" and nothing happened at all — no bubble, and the button still
// offering to do it again.
it("CP-33 Apply with no editor explains itself instead of doing nothing", async () => {
  const editor = fakeEditor();
  open(editor);
  aiChat.mockResolvedValue(proposalResponse());
  render(<Harness />);

  await ask("delete claim 3");
  await waitFor(() => expect(applyButton()).not.toBeNull());
  act(() => {
    useDocumentStore.setState({ editor: null });
  });

  await user().click(applyButton()!);

  await waitFor(() =>
    expect(screen.getByText("There is no open document to edit.")).toBeTruthy(),
  );
  expect(aiApply).not.toHaveBeenCalled();
  expect(screen.getByText("Not applied")).toBeTruthy();
  expect(applyButton()).toBeNull();
});

// CP-34. TxtDropZone sends its rejections to the transcript on the stated grounds
// that the transcript "is already a live region". It was not one, so file
// rejections, AI replies and error bubbles reached a screen reader as silence.
it("CP-34 the transcript is a live region", async () => {
  open(fakeEditor());
  render(<Harness />);

  const transcript = screen.getByTestId("message-list");
  // On the CONTAINER, which exists from mount: an aria-live element added at the
  // same moment as its first child announces nothing.
  expect(transcript.getAttribute("role")).toBe("log");
  expect(transcript.getAttribute("aria-live")).toBe("polite");
});

// CP-35. The server's limit is a good sentence, but it arrives after the round
// trip and after the composer has been cleared — so the typed text is gone.
it("CP-35 the composer caps the instruction at the server's limit and counts up to it", async () => {
  open(fakeEditor());
  render(<Harness />);

  const field = screen.getByLabelText("Ask the AI") as HTMLTextAreaElement;
  expect(field.maxLength).toBe(2_000);

  const person = user();
  await person.click(field);
  await person.paste("x".repeat(1_900));

  expect(screen.getByText("1900 / 2000 characters")).toBeTruthy();
});

// CP-31, second half: the `disabled={sending}` wiring, asserted where it is
// observable. A send always appends a user bubble, so the option bubble stops being
// last and its buttons unmount — the panel can never show them disabled.
it("CP-31 options are inert while a request is in flight", () => {
  const message = {
    id: 1,
    role: "assistant" as const,
    tone: "clarify" as const,
    text: "Which claim did you mean?",
    warnings: [],
    citations: [],
    options: ["Make claim 1 bold."],
    proposal: null,
    resolution: null,
    retry: false,
  };
  const onOption = vi.fn();
  render(
    <Message
      message={message}
      isLast
      sending
      onOption={onOption}
      onRetry={vi.fn()}
      onCitation={vi.fn()}
      onConfirm={vi.fn()}
      onCancel={vi.fn()}
    />,
  );

  expect(screen.getByRole("button", { name: "Make claim 1 bold." })).toHaveProperty(
    "disabled",
    true,
  );
});
