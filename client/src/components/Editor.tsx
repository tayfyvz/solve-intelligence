import { useEffect } from "react";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";

import { useDocumentStore } from "../store";

// Module scope: a fresh array on every render churns the editor's extension setup.
const extensions = [StarterKit];

export interface EditorProps {
  /** Initial content only. The component is remounted per document/version. */
  content: string;
  className?: string;
}

/**
 * An *uncontrolled* editor: TipTap owns the document once it is created, and
 * nothing here ever compares the `content` prop to the editor's current HTML.
 *
 * A sync effect that did compare them fails twice. Mid-typing it is a race: React
 * state is async, so if the editor advances between `onUpdate` and the effect
 * running, `content` is stale and writing it back costs a keystroke and the caret.
 * And `setContent` defaults to `emitUpdate: false`, so the write never reaches the
 * parent — press Save without touching the editor and what gets persisted is the
 * pre-normalisation string, `<!DOCTYPE>`/`<head>`/`<title>` envelope included.
 *
 * Content changes therefore happen by remount: the parent keys this component on
 * `${documentId}:${versionNumber}`, which loads the new content and resets the
 * caret in one mechanism.
 */
export default function Editor({ content, className }: EditorProps) {
  const setEditor = useDocumentStore((s) => s.setEditor);
  const clearEditor = useDocumentStore((s) => s.clearEditor);
  const setDirty = useDocumentStore((s) => s.setDirty);

  // No deps argument: passing [content] would recreate the editor on every
  // keystroke-driven prop change — the same bug in a different hat.
  const editor = useEditor({
    extensions,
    content,
    immediatelyRender: true, // client-only app; also narrows the type to Editor
    onCreate: ({ editor }) => setEditor(editor),
    // The only writer of the dirty flag. The AI path applies its HTML with
    // setContent(html, true), so it flows through here too and ChatPanel never
    // sets it — two writers to one flag is not a thing to debug live.
    onUpdate: () => setDirty(true),
    editorProps: {
      attributes: {
        // Measure, paper and typography all live on `.editor` in index.css — the
        // padding is not a utility here because the sheet needs it responsively.
        class: "editor outline-none",
        "aria-label": "Patent document",
      },
    },
  });

  // `immediatelyRender: true` narrows the hook's return type, so `editor` is never
  // null here. The store's clear is identity-guarded, because React can commit the
  // next key's onCreate before this cleanup runs.
  useEffect(() => () => clearEditor(editor), [editor, clearEditor]);

  return (
    <div className={className}>
      <EditorContent editor={editor} />
    </div>
  );
}
