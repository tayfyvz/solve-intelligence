import { useEffect, useState } from "react";
import type { Editor } from "@tiptap/core";
import type { Node as PMNode } from "@tiptap/pm/model";

import { useDocumentStore } from "../../store";

/** How long the outline and find results may lag the user's typing. Re-deriving ~900 rows
 *  per keystroke is work nobody asked for, and a quarter-second is below "missing". */
const SETTLE_MS = 250;

/** The editor's live document, re-read shortly after it changes. Read-only: it subscribes
 *  to the same `update` event `Editor.onUpdate` fires and hands back the document. */
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
      // Trailing, not leading: a leading edge publishes the document as it was before the
      // keystroke.
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
