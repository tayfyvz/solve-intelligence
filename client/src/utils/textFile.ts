/**
 * One set of `.txt` rules, two jobs: a dropped file is either reference material for the AI
 * (the chat panel's zone, never stored) or a patent to import (the dialog, which creates a
 * document). Name, folder, encoding, BOM and NUL are identical for both and live here once;
 * only the size limit differs, so it is the one thing passed in.
 */
export interface TextFileLimit {
  maxBytes: number;
  /** How the limit is worded when a file exceeds it, in the units the SERVER measures. */
  label: string;
}

/**
 * The client measures file bytes, the server characters. UTF-8 bytes >= chars, so this is
 * strictly stricter and nothing that passes here can 413 there. The label quotes the
 * server's units, so the two never disagree on screen.
 */
export const CONTEXT_LIMIT: TextFileLimit = { maxBytes: 40_000, label: "40,000 characters" };

/** Import is bounded by the save cap, not the AI cap: an imported patent is stored, and the
 *  AI's 40,000 characters would reject an ordinary 60-page patent. */
export const IMPORT_LIMIT: TextFileLimit = { maxBytes: 1_000_000, label: "1,000,000 bytes" };

export interface TextFile {
  name: string;
  /** BOM-stripped, newline-normalised. Interior whitespace untouched. */
  text: string;
  bytes: number;
}

export type TextFileResult = { ok: true; file: TextFile } | { ok: false; error: string };

const ARITY_ERROR = "Please drop one .txt file at a time.";
const FOLDER_ERROR = "Folders can't be attached. Please drop a single .txt file.";
const EMPTY_ERROR = "That file is empty.";
const ENCODING_ERROR =
  "That file isn't valid UTF-8 text. Please save it as UTF-8 and try again.";
const READ_ERROR = "Could not read that file.";

/** SI units, so `kB` matches both the limit labels and the OS file listing. */
export function formatBytes(bytes: number): string {
  return bytes >= 1_000_000
    ? `${(bytes / 1_000_000).toFixed(1)} MB`
    : `${(bytes / 1_000).toFixed(1)} kB`;
}

const isTxtName = (name: string) => name.toLowerCase().endsWith(".txt");

const oversizeError = (bytes: number, limit: TextFileLimit) =>
  // Bytes in, the server's units out, so this is the sentence the server would have sent.
  `That file is ${formatBytes(bytes)}. The limit is ${limit.label}.`;

/** Pure. Every content rule lives here, so it unit-tests without the File API. */
export function validateTextFile(
  name: string,
  raw: string,
  byteLength: number,
  limit: TextFileLimit = CONTEXT_LIMIT,
): TextFileResult {
  if (!isTxtName(name)) {
    return { ok: false, error: `Only .txt files are supported, and "${name}" is not one.` };
  }
  if (byteLength > limit.maxBytes) {
    return { ok: false, error: oversizeError(byteLength, limit) };
  }

  // Strip one leading BOM, then normalise newlines. Interior whitespace is left
  // alone: indentation in a prior-art excerpt is meaning.
  const text = raw.replace(/^\uFEFF/, "").replace(/\r\n?/g, "\n");

  // After the BOM strip: a file containing only a BOM is empty, not three bytes.
  if (text.trim() === "") {
    return { ok: false, error: EMPTY_ERROR };
  }
  // `readAsText` never throws on a mis-encoded file: it substitutes U+FFFD and the AI then
  // reasons over mojibake. UTF-16 is the common case, and scanning for the replacement
  // character is the only cheap detection. NUL means the same thing.
  if (text.includes("\u0000") || text.includes("\uFFFD")) {
    return { ok: false, error: ENCODING_ERROR };
  }

  // `bytes` is what the size check measured, not `text.length`.
  return { ok: true, file: { name, text, bytes: byteLength } };
}

/** Name, folder and size are checked before any read; then FileReader, then the content
 *  rules in `validateTextFile`. */
export async function readTextFile(
  file: File,
  limit: TextFileLimit = CONTEXT_LIMIT,
): Promise<TextFileResult> {
  if (!isTxtName(file.name)) {
    return { ok: false, error: `Only .txt files are supported, and "${file.name}" is not one.` };
  }
  // Before the size check: a dropped folder has `size === 0`, which passes it, and
  // `readAsText` on a directory rejects with a message we can beat.
  if (file.size === 0 && file.type === "") {
    return { ok: false, error: FOLDER_ERROR };
  }
  // Checked before reading, so a 2 GB .txt is never pulled into memory.
  if (file.size > limit.maxBytes) {
    return { ok: false, error: oversizeError(file.size, limit) };
  }

  // Resolves, never rejects, so no caller needs a `try`.
  const text = await new Promise<string | null>((resolve) => {
    const reader = new FileReader();
    reader.onerror = () => resolve(null);
    reader.onabort = () => resolve(null);
    reader.onload = () => resolve(typeof reader.result === "string" ? reader.result : null);
    reader.readAsText(file); // UTF-8 by default — but see the mojibake check above
  });
  if (text === null) return { ok: false, error: READ_ERROR };

  return validateTextFile(file.name, text, file.size, limit);
}

/** Arity, then `readTextFile`. Shared by the drop handler and the file input. */
export async function readDroppedFiles(
  files: FileList | File[] | null,
  limit: TextFileLimit = CONTEXT_LIMIT,
): Promise<TextFileResult> {
  if (!files || files.length !== 1) {
    return { ok: false, error: ARITY_ERROR };
  }
  return readTextFile(files[0], limit);
}
