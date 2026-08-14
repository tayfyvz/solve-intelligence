/**
 * The client measures FILE BYTES (`file.size`); the server measures CHARACTERS
 * (`len(context_text)`). UTF-8 bytes >= chars, so this check is strictly stricter than the
 * server's and nothing that passes here can 413 there. Same number, different units —
 * deliberately, and the message quotes the server's units so the two never disagree on screen.
 */
export const MAX_CONTEXT_BYTES = 40_000;

export interface ContextFile {
  name: string;
  /** BOM-stripped, newline-normalised. Interior whitespace untouched. */
  text: string;
  bytes: number;
}

export type ContextFileResult = { ok: true; file: ContextFile } | { ok: false; error: string };

const ARITY_ERROR = "Please drop one .txt file at a time.";
const FOLDER_ERROR = "Folders can't be attached. Please drop a single .txt file.";
const EMPTY_ERROR = "That file is empty.";
const ENCODING_ERROR =
  "That file isn't valid UTF-8 text. Please save it as UTF-8 and try again.";
const READ_ERROR = "Could not read that file.";

/**
 * SI units, so `kB` means 1000 bytes — the same thing the label says and the same
 * thing the operating system's file listing says. One decimal place, because a
 * rejection message wants a size the user can compare to the limit, not a precise one.
 */
export function formatBytes(bytes: number): string {
  return bytes >= 1_000_000
    ? `${(bytes / 1_000_000).toFixed(1)} MB`
    : `${(bytes / 1_000).toFixed(1)} kB`;
}

const isTxtName = (name: string) => name.toLowerCase().endsWith(".txt");

const oversizeError = (bytes: number) =>
  // Bytes in, characters out. The `40,000` half is a literal so it always matches
  // the server's own 413 text.
  `That file is ${formatBytes(bytes)}. The limit is 40,000 characters.`;

/** Pure. Every content rule lives here, so it unit-tests without the File API. */
export function validateContextText(
  name: string,
  raw: string,
  byteLength: number,
): ContextFileResult {
  if (!isTxtName(name)) {
    return { ok: false, error: `Only .txt files are supported. "${name}" was not attached.` };
  }
  if (byteLength > MAX_CONTEXT_BYTES) {
    return { ok: false, error: oversizeError(byteLength) };
  }

  // Strip one leading BOM, then normalise newlines. Interior whitespace is left
  // alone: indentation in a prior-art excerpt is meaning.
  const text = raw.replace(/^\uFEFF/, "").replace(/\r\n?/g, "\n");

  // Rule 5 runs AFTER the BOM strip — a file containing only a BOM is empty, not
  // three bytes of content.
  if (text.trim() === "") {
    return { ok: false, error: EMPTY_ERROR };
  }
  // `FileReader.readAsText` never throws on a mis-encoded file: it silently
  // substitutes U+FFFD, and the AI then reasons over mojibake. A UTF-16 file is the
  // common real case, and scanning for the replacement character is the only cheap
  // detection. NUL gets the same message — both mean "this is not UTF-8 text".
  if (text.includes("\u0000") || text.includes("\uFFFD")) {
    return { ok: false, error: ENCODING_ERROR };
  }

  // `bytes` is what rule 4 measured, not `text.length`.
  return { ok: true, file: { name, text, bytes: byteLength } };
}

/** Rules 2-4 (name/folder/size) run BEFORE any read; then FileReader, then validateContextText. */
export async function readContextFile(file: File): Promise<ContextFileResult> {
  if (!isTxtName(file.name)) {
    return { ok: false, error: `Only .txt files are supported. "${file.name}" was not attached.` };
  }
  // Rule 3 before rule 4: a dropped folder has `size === 0`, which *passes* the size
  // check, and `readAsText` on a directory entry rejects — the user would get
  // "Could not read that file" for something we can name precisely.
  if (file.size === 0 && file.type === "") {
    return { ok: false, error: FOLDER_ERROR };
  }
  // Checked before reading, so a 2 GB .txt is never pulled into memory.
  if (file.size > MAX_CONTEXT_BYTES) {
    return { ok: false, error: oversizeError(file.size) };
  }

  // Resolves, never rejects, so no caller needs a `try`.
  const text = await new Promise<string | null>((resolve) => {
    const reader = new FileReader();
    reader.onerror = () => resolve(null);
    reader.onabort = () => resolve(null);
    reader.onload = () => resolve(typeof reader.result === "string" ? reader.result : null);
    reader.readAsText(file); // UTF-8 default; see rule 6 for what that does NOT guarantee
  });
  if (text === null) return { ok: false, error: READ_ERROR };

  return validateContextText(file.name, text, file.size);
}

/** Rule 1 (arity) then readContextFile. Shared by the drop handler and the file input. */
export async function readDroppedFiles(
  files: FileList | File[] | null,
): Promise<ContextFileResult> {
  if (!files || files.length !== 1) {
    return { ok: false, error: ARITY_ERROR };
  }
  return readContextFile(files[0]);
}
