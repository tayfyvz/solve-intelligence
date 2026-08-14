import { getSchema } from "@tiptap/core";
import { DOMParser as PMDOMParser, type Node as PMNode } from "@tiptap/pm/model";
import StarterKit from "@tiptap/starter-kit";
import { describe, expect, it } from "vitest";

import { claimSpans, claimsInRange, type ClaimSpan } from "../ai/claims";
import { SEED_1, SEED_2 } from "./seed.fixture";

const schema = getSchema([StarterKit]);

/**
 * A schema-parsed document and no editor at all — `editor.test.tsx` and
 * `seedRoundTrip.test.ts` stay the only suites that mount TipTap in React.
 */
const docOf = (html: string): PMNode => {
  const body = new window.DOMParser().parseFromString(html, "text/html").body;
  return PMDOMParser.fromSchema(schema).parse(body);
};

/** How many top-level blocks each span swallowed — the seed's shape, per C29. */
const blocksPerSpan = (doc: PMNode, spans: ClaimSpan[]): number[] =>
  spans.map((span) => {
    let blocks = 0;
    doc.forEach((node, offset) => {
      if (offset >= span.from && offset + node.nodeSize <= span.to) blocks += 1;
    });
    return blocks;
  });

// X1/X2. The seeds are the only claim documents anyone will actually demo, and their
// block profiles are recorded independently in CLAUDE.md / §2.3 C29 — so this asserts
// against a documented fixture, not against the walker's own opinion.
describe.each([
  { name: "patent 1", html: SEED_1, numbers: [1, 2, 3, 4, 5, 6, 7, 8], blocks: [5, 1, 1, 4, 1, 5, 1, 1] },
  { name: "patent 2", html: SEED_2, numbers: [1, 2, 3, 4, 5, 6, 7, 8, 9], blocks: [6, 1, 1, 1, 1, 1, 5, 1, 1] },
])("X1/X2 claimSpans on seed $name", ({ html, numbers, blocks }) => {
  it("finds every claim, with its continuation paragraphs", () => {
    const doc = docOf(html);
    const spans = claimSpans(doc);

    expect(spans.map((s) => s.number)).toEqual(numbers);
    expect(blocksPerSpan(doc, spans)).toEqual(blocks);
  });
});

// X3. Each guard in CLAIM_PREFIX_RE, by the string that would break without it. Two
// real claims lead, so the >= 2 rule is satisfied and the candidate is judged on its
// own: it must be absorbed as a continuation of claim 2, never open a claim of its own.
describe("X3 claimSpans false-positive guards", () => {
  const candidates = [
    ["a year", "2024. In prior art the device was heavier."],
    ["a parenthesised number", "(1) foo"],
    ["a bare number", "3."],
    ["a decimal", "3.5 mm of travel"],
    ["a colon", "1: foo"],
  ];

  it.each(candidates)("%s does not open a claim", (_label, candidate) => {
    const doc = docOf(`<p>1. A method.</p><p>2. A device.</p><p>${candidate}</p>`);

    expect(claimSpans(doc).map((s) => s.number)).toEqual([1, 2]);
  });
});

// X4. One prefixed paragraph in a prose document is far more likely to be a numbered
// sentence than a claim set. Being conservative means a wrong guess produces no hint
// rather than a wrong one.
it("X4 the >= 2 rule", () => {
  expect(claimSpans(docOf("<p>1. A method of doing things.</p><p>Some prose.</p>"))).toEqual([]);
});

// X5. Untested, `closed` silently swallows every claim in any document with a mid-body
// heading — or, without the `spans.length > 0` guard, the leading "Claims" heading
// closes the run before it starts and the seed patents parse as no claims at all.
describe("X5 a heading terminates the claim run", () => {
  const doc = docOf(
    "<h1>Claims</h1><p>1. A method.</p><p>2. A device.</p><h2>Abstract</h2><p>3. A method of…</p>",
  );

  it("does not claim anything after the heading", () => {
    const spans = claimSpans(doc);

    expect(spans.map((s) => s.number)).toEqual([1, 2]);
    // Not appended to claim 2 either: one block each.
    expect(blocksPerSpan(doc, spans)).toEqual([1, 1]);
  });

  it("is not closed by the leading Claims heading", () => {
    // The same document opens with <h1>Claims</h1>; if that set `closed`, the two
    // spans above would be zero. This is the `if (spans.length > 0)` guard.
    expect(claimSpans(doc).length).toBe(2);
  });
});

// X6. Positions computed by hand, not read off the implementation:
//   "1. Alpha." is 9 characters, so its paragraph is nodeSize 11 → [0, 11)
//   "2. Beta."  is 8                                     → [11, 21)
//   "3. Gamma." is 9                                     → [21, 32)
//   "4. Delta." is 9                                     → [32, 43)
describe("X6 claimsInRange whole vs partial", () => {
  const doc = docOf("<p>1. Alpha.</p><p>2. Beta.</p><p>3. Gamma.</p><p>4. Delta.</p>");

  it("a full claim is whole", () => {
    expect(claimsInRange(doc, 21, 32)).toEqual({ numbers: [3], whole: true });
  });

  it("five characters inside a claim are not", () => {
    expect(claimsInRange(doc, 23, 28)).toEqual({ numbers: [3], whole: false });
  });

  it("two full claims are both, and both whole", () => {
    expect(claimsInRange(doc, 21, 43)).toEqual({ numbers: [3, 4], whole: true });
  });

  it("a range touching no claim names none", () => {
    expect(claimsInRange(docOf("<p>Just prose.</p>"), 1, 5)).toEqual({
      numbers: [],
      whole: false,
    });
  });
});
