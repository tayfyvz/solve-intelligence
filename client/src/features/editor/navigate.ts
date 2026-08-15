import type { Editor } from "@tiptap/core";
import type { Node as PMNode } from "@tiptap/pm/model";

import { claimSpans } from "./claims";

/**
 * Getting around a long patent, which renders as one very tall scroll.
 *
 * Everything here reads `editor.state.doc` and never writes to it: the editor stays
 * uncontrolled and the only thing a jump does to the DOM is `scrollIntoView`. Paginating the
 * contenteditable would buy a page number at the cost of selection, undo and claim resolution.
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

/** The document's headings and claims, in document order. Claims come from `claimSpans`, so
 *  the outline cannot disagree with what the AI thinks claim 12 is. */
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

  // One list, in document order, so claims sit under the heading they belong to.
  return entries.sort((a, b) => a.from - b.from);
}

export interface Match {
  from: number;
  to: number;
}

/**
 * One text block flattened: `positions[i]` is the document position of `text[i]`. Built per
 * character rather than per node, so it spans formatting boundaries like a reader does and
 * stays exact across inline nodes that contribute no characters — a `<br>` occupies a
 * position but no character, which any offset arithmetic would drift on.
 */
function flatten(block: PMNode, blockPos: number): { text: string; positions: number[] } {
  let text = "";
  const positions: number[] = [];
  block.forEach((child, offset) => {
    if (!child.isText || !child.text) return; // a <br> etc: occupies a position, adds no chars
    const start = blockPos + 1 + offset;
    for (let i = 0; i < child.text.length; i += 1) {
      // Lowercased per character, one position per OUTPUT unit: `toLowerCase()` can
      // lengthen a string ("İ" becomes two code units), so lowercasing the block afterwards
      // would leave `positions` short and every later match one character early.
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
 * Searched per text block, so a phrase split by formatting is found exactly as a reader sees
 * it. A phrase spanning two paragraphs is not, and should not be: there is no contiguous
 * range to highlight. `limit` bounds a pathological query, and the caller shows the count so
 * a truncated result is never presented as complete.
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
        // The position after the last matched character — not `positions[index] + length`
        // when a <br> sits inside the match.
        to: positions[index + needle.length - 1] + 1,
      });
      // + needle.length, not + 1: "aa" in "aaaa" is two matches to a reader, not three.
      index = text.indexOf(needle, index + needle.length);
    }
    return false; // a textblock's children are inline; nothing below it to search
  });
  return matches;
}

/** Scrolls a document position into view and reports whether it could. Never focuses,
 *  selects, or dispatches a document transaction: the user may be typing in the find box. */
export function scrollToPosition(editor: Editor, pos: number): boolean {
  if (pos < 0 || pos > editor.state.doc.content.size) return false;
  const { node } = editor.view.domAtPos(Math.min(pos + 1, editor.state.doc.content.size));
  const element = node.nodeType === 1 ? (node as Element) : node.parentElement;
  if (!element) return false;
  // jsdom has no scrollIntoView — stubbed in src/test/setup.ts.
  element.scrollIntoView?.({ block: "center", behavior: "smooth" });
  return true;
}

/** Scrolls a claim into view and returns its range so the caller can highlight it. Same
 *  read-only rule as `scrollToPosition`. */
export function scrollToClaim(editor: Editor, number: number): { from: number; to: number } | null {
  const span = claimSpans(editor.state.doc).find((s) => s.number === number);
  if (!span) return null;
  scrollToPosition(editor, span.from);
  return { from: span.from, to: span.to };
}
