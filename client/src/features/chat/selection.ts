import type { Editor } from "@tiptap/core";
import type { Node as PMNode } from "@tiptap/pm/model";

import type { AiSelection } from "../../types";
import { claimsInRange } from "../editor/claims";

/** `AiSelection` is declared in types.ts, where `test_client_contract.py` can see it. */
export type { AiSelection };

/** Client cap. The server's is 8,000, so nothing that passes here can 413 there. */
export const MAX_SELECTION_CHARS = 4_000;

/**
 * Subscribes to the editor's selection and keeps the last non-empty range: only upgrade,
 * never clear on collapse. Selecting claim 3 and then clicking into the chat box is the
 * normal flow, and a stray click back in the document would otherwise drop the context.
 *
 * The held selection is cleared in three explicit places: the ✕ on the chip, the block that
 * calls `setContent` (a range against the old document is meaningless against the new one),
 * and a user-initiated document or version change.
 */
export function subscribeToSelection(
  editor: Editor,
  onChange: (range: { from: number; to: number } | null) => void,
): () => void {
  const handler = ({ editor }: { editor: Editor }) => {
    const { from, to, empty } = editor.state.selection;
    if (empty) return; // the rule, in one line
    onChange({ from, to });
  };
  editor.on("selectionUpdate", handler);
  return () => {
    editor.off("selectionUpdate", handler);
  };
}

/**
 * Builds the wire shape, pure over (doc, range). Called at send time and never stored: only
 * `{from, to}` is held, so the text sent is whatever is currently in those positions, and a
 * deleted range yields `null` rather than stale text.
 */
export function buildSelectionContext(
  doc: PMNode,
  range: { from: number; to: number },
): AiSelection | null {
  // Block separator "\n", leaf " ", so a multi-paragraph claim arrives as readable lines.
  const raw = doc.textBetween(range.from, range.to, "\n", " ");
  const text = raw.trim();
  if (!text) return null; // a whitespace-only range is not context
  const { numbers, whole } = claimsInRange(doc, range.from, range.to);
  return {
    text: text.slice(0, MAX_SELECTION_CHARS),
    claim_numbers: numbers,
    whole_claims: whole,
    truncated: text.length > MAX_SELECTION_CHARS,
  };
}
