import type { ChainedCommands, Editor } from "@tiptap/core";
import { useEditorState } from "@tiptap/react";

/**
 * One toolbar control. `run` receives an already-focused chain so the same
 * definition drives both the click and the `can()` probe — a button can never
 * claim to be enabled for a command other than the one it runs.
 */
interface Tool {
  /** Visible label. Short on purpose: the toolbar sits above a 44rem sheet. */
  label: string;
  /**
   * Full name for screen readers and the tooltip. It must *contain* `label`
   * (WCAG 2.5.3, Label in Name) — otherwise a speech-input user who says
   * "click H1" cannot press the button they can see.
   */
  name: string;
  /** Extra classes for the label glyph (bold weight, italic, strike-through). */
  labelClass?: string;
  /** True when the caret sits inside this mark/node. Omitted for undo/redo. */
  isActive?: (editor: Editor) => boolean;
  run: (chain: ChainedCommands) => ChainedCommands;
}

// Exactly the StarterKit set that survives the save-path sanitiser allowlist
// (strong, em, s, code, h1-h3, ul/ol/li, blockquote) — a control that produced
// markup the server strips would be a button that silently loses work.
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
  // WHY useEditorState: `useEditor` re-renders on editor *creation*, not on every
  // transaction, so a toolbar reading `isActive` straight from the instance would
  // freeze at whatever the caret was doing on mount. `useEditorState` subscribes to
  // the editor's transaction stream and re-renders only when this selector's result
  // actually changes (deep-equal), which is what makes moving the caret across a
  // <strong> light the Bold button up without polling and without a render per
  // keystroke.
  const states = useEditorState({
    editor,
    selector: ({ editor }) =>
      Object.fromEntries(
        TOOLS.map((tool) => [
          tool.name,
          {
            active: tool.isActive?.(editor) ?? false,
            // `can()` runs the command against a throwaway transaction: true only if
            // it would really apply here (e.g. Redo with nothing to redo, or a list
            // inside a context that forbids it).
            enabled: tool.run(editor.can().chain().focus()).run(),
          },
        ]),
      ) as Record<string, ToolState>,
  });

  return (
    // `role="group"`, not `role="toolbar"`: the toolbar role promises arrow-key
    // roving focus, and these are plain tab-reachable buttons. Claiming the role
    // without the behaviour is worse for a screen-reader user than not claiming it.
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
                // The document loses focus the moment a button takes it, and a
                // blurred ProseMirror has no selection to format. Preventing the
                // default on mousedown stops the browser moving focus at all, so
                // the selection is still there when the click handler runs.
                // Keyboard activation is unaffected: Enter/Space fire click only.
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
