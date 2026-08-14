import type { Node as PMNode } from "@tiptap/pm/model";

/**
 * Mirror of server/app/ai/document.py CLAIM_PREFIX_RE. Guards are load-bearing:
 *  \d{1,3}   — "2024. In prior art…" is a year, not claim 2024
 *  [.)]      — "(1)" / "1:" / "1 -" do not start a claim
 *  \s+(?=\S) — a paragraph that is exactly "3." does not; nor does "3.5 mm of travel"
 *
 * When one of the two changes, the other must.
 */
const CLAIM_PREFIX_RE = /^(\d{1,3})([.)])\s+(?=\S)/;

export interface ClaimSpan {
  number: number;
  from: number;
  to: number;
}

/**
 * Top-level walk only, exactly like the server: a list is one block, `li` is never a claim.
 * A prefix-matching paragraph opens a claim; every following block extends it; a heading closes
 * the run. The >= 2 rule is the server's too — one lone "1. " paragraph in a prose document is
 * far more likely to be a numbered sentence than a claim set, and being conservative here means
 * a wrong guess produces no selection hint rather than a wrong one.
 */
export function claimSpans(doc: PMNode): ClaimSpan[] {
  const spans: ClaimSpan[] = [];
  let open: ClaimSpan | null = null;
  let closed = false;

  doc.forEach((node, offset) => {
    const from = offset;
    const to = offset + node.nodeSize;

    if (node.type.name === "heading") {
      if (open) {
        spans.push(open);
        open = null;
      }
      // A heading after the run has started terminates it; a heading before it (the
      // "Claims" heading itself) simply has no run to close.
      if (spans.length > 0) closed = true;
      return;
    }
    if (closed) return;

    const match = node.type.name === "paragraph" ? CLAIM_PREFIX_RE.exec(node.textContent) : null;
    if (match) {
      if (open) spans.push(open);
      open = { number: Number(match[1]), from, to };
    } else if (open) {
      open.to = to; // a continuation paragraph of the open claim
    }
    // else: preamble. Leading orphans ("What is claimed is:") belong to no claim — matching
    // the server, where they join the preamble so "make claim 1 bold" does not bold them.
  });
  if (open) spans.push(open);

  return spans.length >= 2 ? spans : [];
}

export interface ClaimHit {
  numbers: number[];
  whole: boolean;
}

/** Which claims a [from, to) range touches, and whether it covers each of them entirely. */
export function claimsInRange(doc: PMNode, from: number, to: number): ClaimHit {
  const touched = claimSpans(doc).filter((s) => s.from < to && s.to > from);
  return {
    numbers: touched.map((s) => s.number),
    // The +/-1 tolerance is intentional: selecting a whole paragraph in ProseMirror yields a
    // range that starts at the text position INSIDE the node, so exact equality would make
    // `whole` almost never true. 1 is the node's own boundary token.
    whole: touched.length > 0 && touched.every((s) => from <= s.from + 1 && to >= s.to - 1),
  };
}

// `scrollToClaim` moved to `navigate.ts` when the outline and the find bar needed the
// same "walk to the DOM node and scrollIntoView" step. This file is now purely the claim
// PARSE — the mirror of the server's rule — and nothing in it touches the view.
