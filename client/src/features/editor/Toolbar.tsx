import type { ChainedCommands, Editor } from "@tiptap/core";
import { useEditorState } from "@tiptap/react";

/** One toolbar control. `run` receives an already-focused chain, so one definition drives
 *  both the click and the `can()` probe and a button cannot mis-report itself. */
interface Tool {
  /** Visible label. Short on purpose: the toolbar sits above a 44rem sheet. */
  label: string;
  /** Full name for screen readers and the tooltip. Must contain `label` (WCAG 2.5.3), or a
   *  speech-input user cannot press the button they can see. */
  name: string;
  /** Extra classes for the label glyph (bold weight, italic, strike-through). */
  labelClass?: string;
  /** True when the caret sits inside this mark/node. Omitted for undo/redo. */
  isActive?: (editor: Editor) => boolean;
  run: (chain: ChainedCommands) => ChainedCommands;
}

// Exactly the StarterKit set that survives the save-path sanitiser allowlist: a control
// producing markup the server strips would be a button that silently loses work.
const GROUPS: Tool[][] = [
  [
    { label: "Undo", name: "Undo", run: (c) => c.undo() },
    { label: "Redo", name: "Redo", run: (c) => c.redo() },
  ],
  [
    {
      label: "B",
      name: "Bold",
      labelClass: "font-bold",
      isActive: (e) => e.isActive("bold"),
      run: (c) => c.toggleBold(),
    },
    {
      label: "I",
      name: "Italic",
      labelClass: "italic font-serif",
      isActive: (e) => e.isActive("italic"),
      run: (c) => c.toggleItalic(),
    },
    {
      label: "S",
      name: "Strikethrough",
      labelClass: "line-through",
      isActive: (e) => e.isActive("strike"),
      run: (c) => c.toggleStrike(),
    },
    {
      label: "</>",
      name: "Inline code",
      labelClass: "font-mono text-xs",
      isActive: (e) => e.isActive("code"),
      run: (c) => c.toggleCode(),
    },
  ],
  [
    {
      label: "H1",
      name: "H1 heading",
      isActive: (e) => e.isActive("heading", { level: 1 }),
      run: (c) => c.toggleHeading({ level: 1 }),
    },
    {
      label: "H2",
      name: "H2 heading",
      isActive: (e) => e.isActive("heading", { level: 2 }),
      run: (c) => c.toggleHeading({ level: 2 }),
    },
    {
      label: "H3",
      name: "H3 heading",
      isActive: (e) => e.isActive("heading", { level: 3 }),
      run: (c) => c.toggleHeading({ level: 3 }),
    },
  ],
  [
    {
      label: "• List",
      name: "Bullet list",
      isActive: (e) => e.isActive("bulletList"),
      run: (c) => c.toggleBulletList(),
    },
    {
      label: "1. List",
      name: "1. List (numbered)",
      isActive: (e) => e.isActive("orderedList"),
      run: (c) => c.toggleOrderedList(),
    },
    {
      label: "❝",
      name: "Blockquote",
      isActive: (e) => e.isActive("blockquote"),
      run: (c) => c.toggleBlockquote(),
    },
  ],
];

const TOOLS = GROUPS.flat();

interface ToolState {
  active: boolean;
  enabled: boolean;
}

export interface ToolbarProps {
  editor: Editor;
}

export default function Toolbar({ editor }: ToolbarProps) {
  // `useEditor` re-renders on creation, not per transaction, so reading `isActive` from the
  // instance would freeze the buttons at mount. `useEditorState` re-renders only when this
  // selector's result changes, so the caret crossing a <strong> lights Bold with no polling.
  const states = useEditorState({
    editor,
    selector: ({ editor }) =>
      Object.fromEntries(
        TOOLS.map((tool) => [
          tool.name,
          {
            active: tool.isActive?.(editor) ?? false,
            // `can()` runs the command against a throwaway transaction, so this is true
            // only where it would really apply.
            enabled: tool.run(editor.can().chain().focus()).run(),
          },
        ]),
      ) as Record<string, ToolState>,
  });

  return (
    // `role="group"`, not `role="toolbar"`: that role promises arrow-key roving focus, and
    // these are plain tab-reachable buttons.
    <div className="toolbar" role="group" aria-label="Formatting">
      {GROUPS.map((group, groupIndex) => (
        <div className="toolbar-group" key={groupIndex}>
          {group.map((tool) => {
            const { active, enabled } = states[tool.name];
            return (
              <button
                key={tool.name}
                type="button"
                className="toolbar-btn focus-ring"
                aria-label={tool.name}
                title={tool.name}
                aria-pressed={tool.isActive ? active : undefined}
                data-active={active ? "true" : undefined}
                disabled={!enabled}
                // A blurred ProseMirror has no selection to format, so focus must not
                // move on mousedown. Keyboard activation is unaffected.
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => tool.run(editor.chain().focus()).run()}
              >
                <span className={tool.labelClass}>{tool.label}</span>
              </button>
            );
          })}
        </div>
      ))}
    </div>
  );
}
