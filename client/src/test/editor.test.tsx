import { StrictMode } from "react";
import { render, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import Editor from "../components/Editor";
import editorSource from "../components/Editor.tsx?raw";
import { useDocumentStore } from "../store";

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
