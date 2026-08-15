import { useEffect } from "react";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";

import { highlightKey, highlightPlugin } from "./highlight";
import { useDocumentStore } from "../../store";
import Toolbar from "./Toolbar";

// Module scope: a fresh array on every render churns the editor's extension setup.
const extensions = [StarterKit];

export interface EditorProps {
  /** Initial content only. The component is remounted per document/version. */
  content: string;
}

/**
 * An uncontrolled editor: TipTap owns the document once created, and nothing here compares
 * the `content` prop to the editor's HTML. A sync effect that did would race the user's
 * typing, and `setContent` defaults to `emitUpdate: false`, so its write never reaches the
 * parent. Content changes by remount instead — the parent keys this on
 * `${documentId}:${versionNumber}`, which loads content and resets the caret in one step.
 */
export default function Editor({ content }: EditorProps) {
  const setEditor = useDocumentStore((s) => s.setEditor);
  const clearEditor = useDocumentStore((s) => s.clearEditor);
  const setDirty = useDocumentStore((s) => s.setDirty);

  // No deps argument: [content] would recreate the editor on every keystroke-driven change.
  const editor = useEditor({
    extensions,
    content,
    immediatelyRender: true, // client-only app; also narrows the type to Editor
    // The legacy default re-renders on every transaction, i.e. every keystroke. The toolbar
    // subscribes through `useEditorState` instead, so the blanket re-render buys nothing.
    shouldRerenderOnTransaction: false,
    onCreate: ({ editor }) => setEditor(editor),
    // The only writer of the dirty flag: the AI path uses setContent(html, true), so it
    // flows through here too.
    onUpdate: () => setDirty(true),
    editorProps: {
      attributes: {
        // Measure, paper and typography live on `.editor` in styles/index.css.
        class: "editor outline-none",
        // ARIA forbids naming a bare <div>, so without the role the aria-label is dropped
        // and the editor reaches screen readers unnamed.
        role: "textbox",
        "aria-multiline": "true",
        "aria-label": "Patent document",
      },
    },
  });

  // `immediatelyRender: true` narrows the return type, so `editor` is never null. The
  // store's clear is identity-guarded: React can commit the next key's onCreate first.
  useEffect(() => () => clearEditor(editor), [editor, clearEditor]);

  // The highlight plugin belongs to the editor, not to either consumer: ChatPanel's
  // citation flash and the find bar both paint on it, and ChatPanel unmounts when the chat
  // column is collapsed.
  useEffect(() => {
    if (editor.isDestroyed) return;
    editor.registerPlugin(highlightPlugin());
    return () => {
      if (!editor.isDestroyed) editor.unregisterPlugin(highlightKey);
    };
  }, [editor]);

  return (
    // The toolbar is sticky inside App's scroll container, so it stays reachable through a
    // long claim set without the parent knowing anything about it.
    <div>
      <Toolbar editor={editor} />
      <EditorContent editor={editor} />
    </div>
  );
}
