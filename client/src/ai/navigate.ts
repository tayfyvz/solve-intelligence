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
 * One text block, flattened: its characters, and where each one lives in the document.
 *
 * `positions[i]` is the document position of `text[i]`. Built per CHARACTER rather than
 * per node, which is the whole trick: it spans formatting boundaries like a reader does,
 * and it stays exact across inline nodes that contribute no characters. A `<br>` occupies
 * a position and contributes nothing to the string, so an offset-plus-start calculation
 * would drift by one for every hard break in the paragraph — this cannot, because no
 * position is ever inferred by arithmetic.
 */
function flatten(block: PMNode, blockPos: number): { text: string; positions: number[] } {
  let text = "";
  const positions: number[] = [];
  block.forEach((child, offset) => {
    if (!child.isText || !child.text) return; // a <br> etc: occupies a position, adds no chars
    const start = blockPos + 1 + offset;
    for (let i = 0; i < child.text.length; i += 1) {
      // Lowercased HERE, per character, and one position pushed per OUTPUT unit.
      // `toLowerCase()` can LENGTHEN a string — "İ" (U+0130) becomes two code units — so
      // lowercasing the whole block afterwards leaves `positions` shorter than `text`,
      // every subsequent match lands one character early, and the end position reads off
      // the end of the array as `undefined + 1` = NaN. A NaN decoration does not throw:
      // it silently draws nothing, so the user sees a match count with no highlight.
      const lower = child.text[i].toLowerCase();
      text += lower;
      for (let k = 0; k < lower.length; k += 1) positions.push(start + i);
    }
  });
  return { text, positions };
}

/**
 * Every case-insensitive literal occurrence of `query`, in document order.
 *
 * Searched across each text BLOCK, so a phrase split by formatting — "biocompatible
 * **material**", three text nodes — is found, exactly as a reader sees it. Positions stay
 * exact because `flatten` records one per character instead of computing them from node
 * offsets; the earlier per-text-node version could not match across a mark at all, and
 * the obvious alternative (searching `textContent`) silently misaligns on `<br>`.
 *
 * A phrase spanning two PARAGRAPHS is still not found, and that is correct: they are
 * different blocks with a boundary between them, so there is no contiguous range to
 * highlight.
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
    if (!node.isTextblock) return true; // keep descending: a list holds its paragraphs
    const { text, positions } = flatten(node, pos);
    let index = text.indexOf(needle);
    while (index !== -1 && matches.length < limit) {
      matches.push({
        from: positions[index],
        // The position AFTER the last matched character, which is not
        // `positions[index] + length` when a <br> sits inside the match.
        to: positions[index + needle.length - 1] + 1,
      });
      // + needle.length, not + 1: overlapping hits of "aa" in "aaaa" are two matches to
      // a reader, not three.
      index = text.indexOf(needle, index + needle.length);
    }
    return false; // a textblock's children are inline; nothing below it to search
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
