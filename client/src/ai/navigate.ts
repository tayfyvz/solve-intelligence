import type { Editor } from "@tiptap/core";
import type { Node as PMNode } from "@tiptap/pm/model";

import { claimSpans } from "./claims";

/**
 * Getting around a 37-page patent, which renders as one 43,000-pixel scroll.
 *
 * Everything here READS `editor.state.doc` and never writes to it. That is the whole
 * reason navigation is safe next to invariant 7: the editor stays uncontrolled, no HTML
 * string is ever compared to `getHTML()`, and the only thing a jump does to the DOM is
 * `scrollIntoView`. Paginating or virtualising the contenteditable itself would have
 * bought a page number at the cost of selection, undo and the AI's claim resolution.
 */

export interface OutlineEntry {
  kind: "heading" | "claim";
  /** What the row shows: the heading's text, or "Claim 12". */
  label: string;
  /** Heading level 1-6; 0 for a claim, so a claim never affects indentation. */
  level: number;
  from: number;
  to: number;
}

/** How much of a long heading a row shows before it is cut. */
const MAX_LABEL_CHARS = 70;

/**
 * The document's headings and claims, in document order.
 *
 * Claims come from `claimSpans`, which is already the client's mirror of the server's
 * claim rule — so the outline cannot disagree with what the AI thinks claim 12 is, and
 * there is no third definition of "a claim" to keep in step.
 */
export function documentOutline(doc: PMNode): OutlineEntry[] {
  const entries: OutlineEntry[] = [];

  doc.forEach((node, offset) => {
    if (node.type.name !== "heading") return;
    const text = node.textContent.trim();
    if (!text) return; // an empty heading is a row the user cannot aim at
    entries.push({
      kind: "heading",
      label: text.length > MAX_LABEL_CHARS ? `${text.slice(0, MAX_LABEL_CHARS)}…` : text,
      level: typeof node.attrs.level === "number" ? node.attrs.level : 1,
      from: offset,
      to: offset + node.nodeSize,
    });
  });

  for (const span of claimSpans(doc)) {
    entries.push({
      kind: "claim",
      label: `Claim ${span.number}`,
      level: 0,
      from: span.from,
      to: span.to,
    });
  }

  // One list, in document order, so the reader sees the claims sitting under the Claims
  // heading rather than in a second list they have to correlate by eye.
  return entries.sort((a, b) => a.from - b.from);
}

export interface Match {
  from: number;
  to: number;
}

/**
 * Every case-insensitive literal occurrence of `query`, in document order.
 *
 * Matched inside individual TEXT nodes, which is what makes the positions exact. The
 * cost is the honest one: a phrase split by formatting — "biocompatible **material**" —
 * is three text nodes and is not found. Searching a whole block's `textContent` instead
 * would find it and then be off by one for every `<br>` in the block, and highlighting
 * the wrong words is worse than finding fewer of the right ones.
 *
 * `limit` bounds the work on a pathological query ("e" in a 900-claim patent); the
 * caller shows the count so a truncated result is never silently presented as complete.
 */
export function findMatches(doc: PMNode, query: string, limit = 500): Match[] {
  const needle = query.toLowerCase();
  if (!needle) return [];

  const matches: Match[] = [];
  doc.descendants((node, pos) => {
    if (matches.length >= limit) return false;
    if (!node.isText || !node.text) return;
    const haystack = node.text.toLowerCase();
    let index = haystack.indexOf(needle);
    while (index !== -1 && matches.length < limit) {
      matches.push({ from: pos + index, to: pos + index + needle.length });
      // + needle.length, not + 1: overlapping hits of "aa" in "aaaa" are two matches to
      // a reader, not three.
      index = haystack.indexOf(needle, index + needle.length);
    }
  });
  return matches;
}

/**
 * Scrolls a document position into view and returns whether it could.
 *
 * Never focuses, never selects, and never dispatches a document transaction — the user
 * may be typing in the find box, and moving the caret out of it would make find unusable.
 */
export function scrollToPosition(editor: Editor, pos: number): boolean {
  if (pos < 0 || pos > editor.state.doc.content.size) return false;
  const { node } = editor.view.domAtPos(Math.min(pos + 1, editor.state.doc.content.size));
  const element = node.nodeType === 1 ? (node as Element) : node.parentElement;
  if (!element) return false;
  // jsdom has no scrollIntoView — stubbed in src/test/setup.ts.
  element.scrollIntoView?.({ block: "center", behavior: "smooth" });
  return true;
}

/**
 * Scrolls a claim into view and returns its range so the caller can highlight it.
 * Deliberately does NOT focus, does not select, and does not dispatch a document
 * transaction: clicking a citation while typing in the chat box must not move the caret
 * out of the box.
 */
export function scrollToClaim(editor: Editor, number: number): { from: number; to: number } | null {
  const span = claimSpans(editor.state.doc).find((s) => s.number === number);
  if (!span) return null;
  scrollToPosition(editor, span.from);
  return { from: span.from, to: span.to };
}
