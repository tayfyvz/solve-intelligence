import { useEffect, useState } from "react";
import type { Editor } from "@tiptap/core";
import type { Node as PMNode } from "@tiptap/pm/model";

import { useDocumentStore } from "./store";

/**
 * How long the outline and the find results may lag the user's typing.
 *
 * The outline of a 900-claim patent is ~900 rows, and re-deriving and re-rendering them
 * on every keystroke is work nobody asked for while someone is mid-sentence. A quarter of
 * a second is below the threshold at which a heading you just typed feels "missing" and
 * far above a typing cadence.
 */
const SETTLE_MS = 250;

/**
 * The editor's live document, re-read shortly after it changes.
 *
 * READ ONLY, and that is what keeps it compatible with invariant 7. Nothing here writes
 * to the editor, nothing compares an HTML string to `getHTML()`, and content still
 * changes only by remount — this subscribes to the same `update` event `Editor.onUpdate`
 * already fires and hands back the ProseMirror document for navigation to look at.
 */
export function useEditorDoc(): PMNode | null {
  const editor = useDocumentStore((s) => s.editor);
  const [doc, setDoc] = useState<PMNode | null>(null);

  useEffect(() => {
    if (!editor || editor.isDestroyed) {
      setDoc(null);
      return;
    }
    setDoc(editor.state.doc); // the state at mount, before any edit
    let timer: number | null = null;
    const onUpdate = ({ editor: live }: { editor: Editor }) => {
      if (timer !== null) window.clearTimeout(timer);
      // Trailing, not leading: the value that matters is the one after the user stops,
      // and a leading edge would publish the document as it was BEFORE the keystroke.
      timer = window.setTimeout(() => {
        if (!live.isDestroyed) setDoc(live.state.doc);
      }, SETTLE_MS);
    };
    editor.on("update", onUpdate);
    return () => {
      if (timer !== null) window.clearTimeout(timer);
      editor.off("update", onUpdate);
    };
  }, [editor]);

  return doc;
}
