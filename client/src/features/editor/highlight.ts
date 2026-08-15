import type { Editor } from "@tiptap/core";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";

/**
 * Repaints a selection the browser stopped drawing.
 *
 * ProseMirror's DOM observer ignores `selectionchange` while the view is unfocused, so clicking
 * into the chat box leaves `state.selection` intact and only the *visual* highlight disappears.
 * This plugin is cosmetic; the selection itself is never read from here.
 */
export const highlightKey = new PluginKey<DecorationSet>("aiHighlight");

export type HighlightMeta =
  | { kind: "selection" | "citation"; from: number; to: number }
  | { kind: "clear" };

export function highlightPlugin(): Plugin<DecorationSet> {
  return new Plugin<DecorationSet>({
    key: highlightKey,
    state: {
      init: () => DecorationSet.empty,
      apply(tr, set) {
        const meta = tr.getMeta(highlightKey) as HighlightMeta | undefined;
        // `meta.from` does not exist on the `clear` variant, so removing this line is a
        // compile error — tsc is the guard, since the two paths look alike at runtime.
        if (meta?.kind === "clear") return DecorationSet.empty;
        if (meta) {
          return DecorationSet.create(tr.doc, [
            Decoration.inline(meta.from, meta.to, { class: `ai-hl ai-hl-${meta.kind}` }),
          ]);
        }
        // No meta: map through the change so the band tracks edited text instead of
        // sliding off it.
        return set.map(tr.mapping, tr.doc);
      },
    },
    props: { decorations: (state) => highlightKey.getState(state) },
  });
}

/** Meta-only: `docChanged` is false, so this can never reach Editor.onUpdate or the dirty flag. */
export function paint(editor: Editor, meta: HighlightMeta): void {
  editor.view.dispatch(editor.state.tr.setMeta(highlightKey, meta));
}
