/**
 * The client-side format fast-path.
 *
 * "Make the selected text italic" is a formatting command with a selection already
 * attached. Sending it to the model costs ~1.5 s and an API call to do what
 * `setMark` does synchronously, so this never leaves the browser.
 *
 * Deliberately narrow. Anything this does not match with high confidence falls through to the
 * server, which is the safe direction: a false negative costs a round-trip, a false positive
 * silently does the wrong thing to the document.
 *
 * It lives here rather than in `ChatPanel.tsx` because it is a pure function the gate
 * tests call directly, with no panel (CP-29) — and because a non-component export
 * from a component file trips `react-refresh/only-export-components`, which this
 * repo lints at `--max-warnings 0`.
 */
const FORMAT_RE =
  /^\s*(?:please\s+)?(?:can\s+you\s+)?(?:make|set|turn|format)\s+(?:(?:the|this|that)\s+)?(?:selection|selected(?:\s+text)?|highlighted(?:\s+text)?|it|this|that)(?:\s+(?:in)?to)?\s+(bold|boldface|italic|italics|strikethrough|struck\s*through)\s*[.!?]?\s*$/i;

const UNFORMAT_RE =
  /^\s*(?:please\s+)?(?:remove|clear|un-?set|un-?make)\s+(?:the\s+)?(bold|italic|italics|strikethrough|struck\s*through)(?:\s+(?:from|on)\s+(?:the\s+)?(?:selection|selected(?:\s+text)?|this|that|it))?\s*[.!?]?\s*$/i;

const MARKS: Record<string, string> = {
  bold: "bold",
  boldface: "bold",
  italic: "italic",
  italics: "italic",
  strikethrough: "strike",
  struckthrough: "strike",
};

/** null = "not a local format command, send it to the server". */
export function localFormat(instruction: string): { mark: string; on: boolean } | null {
  // Any mention of a claim, or a scope word, means this is not about the current
  // selection. Measured by mutation: against the two anchored patterns below this
  // clause is currently UNREACHABLE — every instruction it would reject is already
  // rejected by them. It is kept as defence in depth for the day someone widens a
  // pattern, which is exactly when a false positive would start formatting the wrong
  // thing. Do not read CP-29's null rows as proof that this line works (§26.12 row 31).
  if (/\bclaims?\b|\ball\b|\bevery\b|\beach\b|\bwhole\b|\bentire\b|\bdocument\b/i.test(instruction))
    return null;
  const on = FORMAT_RE.exec(instruction);
  if (on) return { mark: MARKS[on[1].toLowerCase().replace(/\s+/g, "")], on: true };
  const off = UNFORMAT_RE.exec(instruction);
  if (off) return { mark: MARKS[off[1].toLowerCase().replace(/\s+/g, "")], on: false };
  return null;
}
