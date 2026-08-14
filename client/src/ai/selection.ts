import type { Editor } from "@tiptap/core";
import type { Node as PMNode } from "@tiptap/pm/model";

import type { AiSelection } from "../types";
import { claimsInRange } from "./claims";

/** `AiSelection` is declared in types.ts, where `test_client_contract.py` can see it. */
export type { AiSelection };

/**
 * Client cap. The server's is 8_000 (§23.1), so the client is strictly stricter and nothing
 * that passes here can 413 there — the same asymmetry as contextFile's byte/char cap.
 */
export const MAX_SELECTION_CHARS = 4_000;

/**
 * Subscribes to the editor's selection and keeps the last NON-EMPTY range.
 *
 * The rule: **only upgrade, never clear on collapse.** Selecting claim 3 and then clicking into
 * the chat box is the normal flow, and a stray click back in the document — or any command that
 * collapses the selection — would otherwise silently drop the context the user set up. So a
 * collapsed selection is ignored; only a new non-empty selection replaces the held one.
 *
 * The held selection is cleared in exactly three places, all explicit:
 *   1. the user clicks the ✕ on the selection chip;
 *   2. the same block that calls `setContent` (§26.4) — a range captured against the OLD
 *      document is meaningless against the new one;
 *   3. a user-initiated document or version change (§26.6).
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
 * Builds the wire shape. Pure over (doc, range) — this is what the tests exercise.
 *
 * Called at send time, from the live doc, and never stored: a built `AiSelection` would go
 * stale the moment the user typed. Only `{from, to}` is held, so the text sent is always the
 * text currently in those positions — and if the user deleted the range, this returns `null`
 * and the request simply carries no selection.
 */
export function buildSelectionContext(
  doc: PMNode,
  range: { from: number; to: number },
): AiSelection | null {
  // Block separator "\n", leaf text " ", so a multi-paragraph claim arrives as readable
  // lines rather than one run-on string.
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
