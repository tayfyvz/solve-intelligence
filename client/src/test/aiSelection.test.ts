import type { Editor } from "@tiptap/core";
import { getSchema } from "@tiptap/core";
import { DOMParser as PMDOMParser, type Node as PMNode } from "@tiptap/pm/model";
import { EditorState } from "@tiptap/pm/state";
import { DecorationSet, type Decoration } from "@tiptap/pm/view";
import StarterKit from "@tiptap/starter-kit";
import { describe, expect, it, vi } from "vitest";

import { highlightKey, highlightPlugin } from "../ai/highlight";
import { buildSelectionContext, subscribeToSelection } from "../ai/selection";

const schema = getSchema([StarterKit]);

const docOf = (html: string): PMNode => {
  const body = new window.DOMParser().parseFromString(html, "text/html").body;
  return PMDOMParser.fromSchema(schema).parse(body);
};

// X7. The cap is the client's half of the 4_000 / 8_000 asymmetry: nothing that passes
// here can 413 on the server.
describe("X7 buildSelectionContext truncation", () => {
  it("truncates at MAX_SELECTION_CHARS and says so", () => {
    const doc = docOf(`<p>${"a".repeat(5_000)}</p>`);

    const context = buildSelectionContext(doc, { from: 1, to: 5_001 });

    expect(context?.text.length).toBe(4_000);
    expect(context?.truncated).toBe(true);
  });

  it("does not truncate what fits", () => {
    const doc = docOf("<p>1. A method.</p><p>2. A device.</p>");

    expect(buildSelectionContext(doc, { from: 0, to: 13 })).toEqual({
      text: "1. A method.",
      claim_numbers: [1],
      whole_claims: true,
      truncated: false,
    });
  });

  it("a whitespace-only range is not context", () => {
    // Built through the schema, not parsed from HTML: the HTML parser collapses runs
    // of spaces, so "a   b" in markup would arrive as "a b" and this range would
    // straddle a letter.
    const doc = schema.node("doc", null, [
      schema.node("paragraph", null, [schema.text("a   b")]),
    ]);

    // "a   b" sits at positions 1..6, so 2..5 is the three spaces alone.
    expect(buildSelectionContext(doc, { from: 2, to: 5 })).toBeNull();
  });
});

/** Emits `selectionUpdate` the way TipTap does, with no editor anywhere near it. */
const fakeEditor = () => {
  const handlers: ((payload: { editor: Editor }) => void)[] = [];
  const state = { selection: { from: 0, to: 0, empty: true } };
  const editor = {
    state,
    on: (_event: string, handler: (payload: { editor: Editor }) => void) => {
      handlers.push(handler);
    },
    off: (_event: string, handler: (payload: { editor: Editor }) => void) => {
      handlers.splice(handlers.indexOf(handler), 1);
    },
  };
  return {
    editor: editor as unknown as Editor,
    emit(selection: { from: number; to: number; empty: boolean }) {
      state.selection = selection;
      handlers.forEach((handler) => handler({ editor: editor as unknown as Editor }));
    },
  };
};

// X8. Selecting claim 3 and then clicking into the chat box is the normal flow. If a
// collapsed selection cleared the held range, the context the user just set up would
// vanish on the click that let them describe what to do with it.
describe("X8 only upgrade, never clear on collapse", () => {
  it("ignores a collapsed selection", () => {
    const onChange = vi.fn();
    const { editor, emit } = fakeEditor();
    subscribeToSelection(editor, onChange);

    emit({ from: 4, to: 20, empty: false });
    emit({ from: 7, to: 7, empty: true });

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith({ from: 4, to: 20 });
  });

  it("unsubscribes", () => {
    const onChange = vi.fn();
    const { editor, emit } = fakeEditor();

    subscribeToSelection(editor, onChange)();
    emit({ from: 4, to: 20, empty: false });

    expect(onChange).not.toHaveBeenCalled();
  });
});

/** Decoration attributes are not in the public typings; the class lives on the type. */
const classOf = (decoration: Decoration): string =>
  (decoration as unknown as { type: { attrs: { class: string } } }).type.attrs.class;

const bandOf = (set: DecorationSet | undefined): Decoration | undefined => set?.find()[0];

// X9. Drives the plugin's `apply` directly, no editor. (b) is the one that matters: a
// band that does not map through edits slides off the text it marks.
describe("X9 the highlight plugin maps through edits and clears on meta", () => {
  const initial = () =>
    EditorState.create({ doc: docOf("<p>Hello world</p>"), plugins: [highlightPlugin()] });

  it("paints one decoration for a selection", () => {
    const painted = initial().apply(
      initial().tr.setMeta(highlightKey, { kind: "selection", from: 1, to: 6 }),
    );
    const band = bandOf(highlightKey.getState(painted));

    expect([band?.from, band?.to]).toEqual([1, 6]);
    expect(classOf(band!)).toBe("ai-hl ai-hl-selection");
  });

  it("shifts the band when text is inserted before it", () => {
    const start = initial();
    const painted = start.apply(
      start.tr.setMeta(highlightKey, { kind: "selection", from: 1, to: 6 }),
    );

    const edited = painted.apply(painted.tr.insertText("XXXX", 1));
    const band = bandOf(highlightKey.getState(edited));

    expect([band?.from, band?.to]).toEqual([5, 10]);
  });

  it("clears on the clear meta", () => {
    const start = initial();
    const painted = start.apply(
      start.tr.setMeta(highlightKey, { kind: "citation", from: 1, to: 6 }),
    );

    const cleared = painted.apply(painted.tr.setMeta(highlightKey, { kind: "clear" }));

    // Identity, not emptiness: without the `clear` branch the meta falls through to
    // `DecorationSet.create` and builds a decoration out of two undefined positions,
    // whose `find()` is also empty. That set is not `DecorationSet.empty`.
    expect(highlightKey.getState(cleared)).toBe(DecorationSet.empty);
  });

  // The entire reason a decoration plugin is acceptable in a codebase with a
  // one-writer dirty rule: a meta-only transaction cannot reach Editor.onUpdate.
  it("never marks the document changed", () => {
    const start = initial();
    const paint = start.tr.setMeta(highlightKey, { kind: "selection", from: 1, to: 6 });
    const clear = start.tr.setMeta(highlightKey, { kind: "clear" });

    expect(paint.docChanged).toBe(false);
    expect(clear.docChanged).toBe(false);
  });
});
