import { StrictMode } from "react";
import { fireEvent, render, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it } from "vitest";

import Editor from "../components/Editor";
import editorSource from "../components/Editor.tsx?raw";
import { useDocumentStore } from "../store";

// jsdom has no layout engine, and `Range.prototype.getClientRects` is missing
// outright. Any toolbar command chains `.focus()`, which makes ProseMirror scroll
// the selection into view and ask the DOM for rectangles — that throws, out of band,
// and fails the run even though every assertion passed. Nothing asserted here
// depends on geometry, so empty rectangles are the honest stub.
beforeAll(() => {
  const empty = { length: 0, item: () => null } as unknown as DOMRectList;
  Range.prototype.getClientRects = () => empty;
  Range.prototype.getBoundingClientRect = () =>
    ({ top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0, x: 0, y: 0 }) as DOMRect;
});

/** TipTap emits `onCreate` a tick after the element is in the DOM. */
const mountedEditor = () =>
  waitFor(() => {
    const editor = useDocumentStore.getState().editor;
    expect(editor).not.toBeNull();
    return editor!;
  });

describe("Editor", () => {
  // E1. "" is a legal version — a document the user emptied and saved. It is also
  // falsy, so a `content && ...` guard anywhere on this path would make such a
  // document permanently unloadable. This is the likeliest stress-test failure in
  // the client, so it gets the one mount test.
  it('mounts an empty version ("")', async () => {
    const { getByLabelText } = render(<Editor content="" />);

    expect(getByLabelText("Patent document")).toBeTruthy();
    const editor = await mountedEditor();
    expect(editor.getHTML()).toBe("<p></p>");
  });

  // The remount contract: content changes reach the editor by `key`, never by a
  // prop write. A sync effect of any spelling — comparing first or not — would
  // stamp the prop over the document here and take the caret with it.
  it("ignores a changed content prop without a remount", async () => {
    const { rerender } = render(<Editor content="<p>Hello</p>" />);
    const editor = await mountedEditor();

    rerender(<Editor content="<p>Something else entirely</p>" />);

    expect(editor.getHTML()).toBe("<p>Hello</p>");
  });

  // main.tsx wraps the app in StrictMode, whose simulated unmount/remount runs the
  // cleanup below without a second `onCreate`. If the store were left null there,
  // Save and the chat would be dead in dev and nowhere else.
  it("leaves the editor registered under StrictMode", async () => {
    render(
      <StrictMode>
        <Editor content="<p>Hello</p>" />
      </StrictMode>,
    );

    const editor = await mountedEditor();
    expect(editor.getHTML()).toBe("<p>Hello</p>");
  });

  it("registers and then clears itself in the store", async () => {
    const { unmount } = render(<Editor content="<p>Hello</p>" />);
    const editor = await mountedEditor();
    expect(editor.getHTML()).toBe("<p>Hello</p>");

    unmount();
    expect(useDocumentStore.getState().editor).toBeNull();
  });

  // The dirty flag has exactly one writer, and this is it. (The mount half —
  // "a freshly parsed document is not dirty" — is asserted here too: it holds
  // before the edit, and TipTap emits no `update` for the initial parse.)
  it("marks the document dirty when the editor content changes", async () => {
    render(<Editor content="<p>Hello</p>" />);
    const editor = await mountedEditor();
    expect(useDocumentStore.getState().dirty).toBe(false);

    editor.commands.setContent("<p>Hello there</p>", true);

    expect(useDocumentStore.getState().dirty).toBe(true);
  });

  // E2. Invariant 7 is otherwise enforced only by prose, and the regression is one
  // well-meant useEffect away, so it is asserted against the source itself.
  it("contains no getHTML() comparison", () => {
    expect(editorSource).not.toContain("getHTML()");
  });
});

describe("Editor toolbar", () => {
  // T1. The whole point of the toolbar: a user who never learned Cmd+B selects text,
  // clicks, and the selection — not the document, not nothing — gets the mark.
  it("applies bold to the current selection when Bold is clicked", async () => {
    const { getByLabelText } = render(<Editor content="<p>Hello world</p>" />);
    const editor = await mountedEditor();

    editor.commands.setTextSelection({ from: 1, to: 6 }); // "Hello"
    fireEvent.click(getByLabelText("Bold"));

    expect(editor.getHTML()).toBe("<p><strong>Hello</strong> world</p>");
  });

  // T2. Focus preservation. A button that took focus would blur ProseMirror and
  // format an empty selection, so the mousedown default must be prevented.
  // `fireEvent` returns false when a handler called preventDefault.
  it("does not steal focus from the document on mousedown", async () => {
    const { getByLabelText } = render(<Editor content="<p>Hello world</p>" />);
    await mountedEditor();

    expect(fireEvent.mouseDown(getByLabelText("Bold"))).toBe(false);
  });

  // T3. Active state has to track the caret, which means the toolbar re-renders on
  // selection-only transactions — the thing `useEditor` alone does not do.
  it("reflects the mark under the cursor", async () => {
    const { getByLabelText } = render(
      <Editor content="<p><strong>Bold</strong> plain</p>" />,
    );
    const editor = await mountedEditor();
    const bold = getByLabelText("Bold");

    editor.commands.setTextSelection(3); // inside <strong>
    await waitFor(() => expect(bold.getAttribute("aria-pressed")).toBe("true"));

    editor.commands.setTextSelection(9); // inside " plain"
    await waitFor(() => expect(bold.getAttribute("aria-pressed")).toBe("false"));
  });

  // T4. A command that cannot run is disabled rather than a no-op click: nothing has
  // been typed, so there is no history to undo.
  it("disables a command that cannot run", async () => {
    const { getByLabelText } = render(<Editor content="<p>Hello world</p>" />);
    const editor = await mountedEditor();
    const undo = getByLabelText("Undo") as HTMLButtonElement;

    expect(undo.disabled).toBe(true);
    fireEvent.click(undo);
    expect(editor.getHTML()).toBe("<p>Hello world</p>");
  });

  // T5. Undo/redo are toolbar buttons because a mouse-driven user has no Cmd+Z
  // habit, and a formatting mistake must be reversible without one.
  it("undoes and redoes a toolbar edit", async () => {
    const { getByLabelText } = render(<Editor content="<p>Hello world</p>" />);
    const editor = await mountedEditor();

    editor.commands.setTextSelection({ from: 1, to: 6 });
    fireEvent.click(getByLabelText("Bold"));
    expect(editor.getHTML()).toBe("<p><strong>Hello</strong> world</p>");

    const undo = getByLabelText("Undo") as HTMLButtonElement;
    await waitFor(() => expect(undo.disabled).toBe(false));
    fireEvent.click(undo);
    expect(editor.getHTML()).toBe("<p>Hello world</p>");

    const redo = getByLabelText("Redo") as HTMLButtonElement;
    await waitFor(() => expect(redo.disabled).toBe(false));
    fireEvent.click(redo);
    expect(editor.getHTML()).toBe("<p><strong>Hello</strong> world</p>");
  });

  // T6. Toolbar edits flow through TipTap's transaction stream, so they reach the
  // store through `Editor.onUpdate` — the single dirty writer. No second writer.
  it("marks the document dirty through the existing single writer", async () => {
    const { getByLabelText } = render(<Editor content="<p>Hello world</p>" />);
    const editor = await mountedEditor();
    expect(useDocumentStore.getState().dirty).toBe(false);

    editor.commands.setTextSelection({ from: 1, to: 6 });
    fireEvent.click(getByLabelText("Bold"));

    expect(useDocumentStore.getState().dirty).toBe(true);
  });
});
