import { getSchema } from "@tiptap/core";
import { DOMParser as PMDOMParser, type Node as PMNode } from "@tiptap/pm/model";
import StarterKit from "@tiptap/starter-kit";
import { describe, expect, it } from "vitest";

import { documentOutline, findMatches } from "../ai/navigate";
import { SEED_1 } from "./seed.fixture";

const schema = getSchema([StarterKit]);

/** A schema-parsed document and no editor at all — same rig as `aiClaims.test.ts`. */
const docOf = (html: string): PMNode => {
  const body = new window.DOMParser().parseFromString(html, "text/html").body;
  return PMDOMParser.fromSchema(schema).parse(body);
};

/** The shape the navigator exists for: 60 claims under real section headings. */
const LONG = [
  "<h1>FIELD OF THE INVENTION</h1><p>Extracorporeal circulation.</p>",
  "<h1>BACKGROUND</h1><p>Prior oxygenators have a large priming volume.</p>",
  "<h2>Related Art</h2><p>US 1,234,567 discloses a membrane.</p>",
  "<h1>Claims</h1>",
  ...Array.from({ length: 60 }, (_, i) => `<p>${i + 1}. A device of type ${i + 1}.</p>`),
].join("");

describe("N1 documentOutline", () => {
  it("lists every heading and every claim, in document order", () => {
    const entries = documentOutline(docOf(LONG));

    // Four headings and sixty claims: the whole document, reachable in one click each.
    expect(entries.filter((e) => e.kind === "heading").map((e) => e.label)).toEqual([
      "FIELD OF THE INVENTION",
      "BACKGROUND",
      "Related Art",
      "Claims",
    ]);
    expect(entries.filter((e) => e.kind === "claim")).toHaveLength(60);

    // ORDER is the property that makes it readable: claims sit under the Claims heading
    // rather than in a second list the reader has to correlate by eye.
    const positions = entries.map((e) => e.from);
    expect([...positions].sort((a, b) => a - b)).toEqual(positions);
    expect(entries[3].label).toBe("Claims");
    expect(entries[4].label).toBe("Claim 1");
    expect(entries[entries.length - 1].label).toBe("Claim 60");
  });

  it("carries the heading level, so the outline can indent", () => {
    const entries = documentOutline(docOf(LONG));
    expect(entries.find((e) => e.label === "Related Art")?.level).toBe(2);
    expect(entries.find((e) => e.label === "BACKGROUND")?.level).toBe(1);
    // A claim never affects indentation, so it has no level of its own.
    expect(entries.find((e) => e.kind === "claim")?.level).toBe(0);
  });

  it("agrees with the claim parser on the seed, rather than having a second opinion", () => {
    const claims = documentOutline(docOf(SEED_1)).filter((e) => e.kind === "claim");
    expect(claims.map((e) => e.label)).toEqual([
      "Claim 1",
      "Claim 2",
      "Claim 3",
      "Claim 4",
      "Claim 5",
      "Claim 6",
      "Claim 7",
      "Claim 8",
    ]);
  });

  it("skips an empty heading, which is a row nobody can aim at", () => {
    expect(documentOutline(docOf("<h1></h1><p>text</p>"))).toEqual([]);
  });
});

describe("N2 findMatches", () => {
  it("finds every case-insensitive occurrence, in document order", () => {
    const doc = docOf("<p>Priming volume matters.</p><p>The PRIMING volume again.</p>");
    const matches = findMatches(doc, "priming");

    expect(matches).toHaveLength(2);
    expect(matches[0].from).toBeLessThan(matches[1].from);
    // The positions are EXACT, which is the whole reason this matches inside text nodes:
    // an off-by-one highlights the wrong words, which is worse than finding fewer.
    for (const match of matches) {
      expect(doc.textBetween(match.from, match.to).toLowerCase()).toBe("priming");
    }
  });

  it("counts overlapping runs the way a reader does", () => {
    expect(findMatches(docOf("<p>aaaa</p>"), "aa")).toHaveLength(2);
  });

  it("returns nothing for an empty query rather than every position", () => {
    expect(findMatches(docOf("<p>text</p>"), "")).toEqual([]);
    expect(findMatches(docOf("<p>text</p>"), "absent")).toEqual([]);
  });

  it("stops at the limit, so a one-letter query cannot walk a whole patent", () => {
    const matches = findMatches(docOf(LONG), "e", 25);
    expect(matches).toHaveLength(25);
  });

  it("does not match across a formatting boundary, and that is the documented trade", () => {
    // "biocompatible material" split by <strong> is three text nodes. Searching a whole
    // block's textContent would find it and then be off by one for every <br> in the
    // block — highlighting the wrong span is worse than finding fewer of the right ones.
    const doc = docOf("<p>a <strong>bio</strong>compatible material</p>");
    expect(findMatches(doc, "biocompatible")).toEqual([]);
    expect(findMatches(doc, "compatible material")).toHaveLength(1);
  });
});
