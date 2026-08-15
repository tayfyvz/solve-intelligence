/**
 * The client-side format fast-path. "Make the selected text italic" is a formatting command
 * with a selection already attached, so sending it to the model costs a round-trip to do what
 * `setMark` does synchronously.
 *
 * Deliberately narrow: anything not matched with high confidence falls through to the server,
 * which is the safe direction — a false negative costs a round-trip, a false positive
 * silently does the wrong thing to the document.
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
  // A claim or a scope word means this is not about the current selection. The two anchored
  // patterns below already reject everything this catches; it is kept as defence in depth
  // for the day one of them is widened.
  if (/\bclaims?\b|\ball\b|\bevery\b|\beach\b|\bwhole\b|\bentire\b|\bdocument\b/i.test(instruction))
    return null;
  const on = FORMAT_RE.exec(instruction);
  if (on) return { mark: MARKS[on[1].toLowerCase().replace(/\s+/g, "")], on: true };
  const off = UNFORMAT_RE.exec(instruction);
  if (off) return { mark: MARKS[off[1].toLowerCase().replace(/\s+/g, "")], on: false };
  return null;
}
