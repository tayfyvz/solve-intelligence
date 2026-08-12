import { Editor } from "@tiptap/core";
import StarterKit from "@tiptap/starter-kit";
import { describe, expect, it } from "vitest";
import { SEED_1, SEED_2 } from "./seed.fixture";

/**
 * The safety net the whole AI layer rests on: the seed stored by the backend is
 * byte-identical to what the real editor emits, so the parser sees one shape
 * before the first save and after it.
 */
const roundTrip = (html: string): string => {
  const editor = new Editor({ content: html, extensions: [StarterKit] });
  const out = editor.getHTML();
  editor.destroy();
  return out;
};

const seeds = [
  { name: "patent 1", html: SEED_1, paragraphs: 19, claims: 8 },
  { name: "patent 2", html: SEED_2, paragraphs: 18, claims: 9 },
];

describe.each(seeds)("$name seed", ({ html, paragraphs, claims }) => {
  it("is byte-identical after a TipTap round-trip", () => {
    expect(roundTrip(html)).toBe(html);
  });

  it("is stored in getHTML() shape", () => {
    expect(html).not.toContain("\n");
    expect(html).not.toContain("<!DOCTYPE");
    expect(html).not.toContain("<title>");
    expect(html).not.toContain("<body>");
    // Whitespace between adjacent tags would survive the round-trip in some
    // places and not others; there must be none.
    expect(html).not.toContain("> <");
    expect(html.startsWith("<h1>Claims</h1>")).toBe(true);
  });

  it("has the expected structure", () => {
    expect(html.match(/<p>/g)).toHaveLength(paragraphs);
    expect(html.match(/<p>\d+\. /g)).toHaveLength(claims);
  });
});
