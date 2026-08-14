import { useRef, useState } from "react";

import { importText, toMessage } from "../api";
import { IMPORT_LIMIT, formatBytes, readDroppedFiles, type TextFile } from "../textFile";
import type { TextImportResult } from "../types";
import Modal from "./Modal";
import { SpinnerLabel } from "./Spinner";

export type ImportDestination = "document" | "version";

export interface ImportPatentDialogProps {
  /** The open patent's title, or null when nothing is open — which is also the only
   *  thing that decides whether "new version" is offered at all. */
  openPatentTitle: string | null;
  /**
   * Commits the conversion. Resolves to an error sentence, or null on success, in which
   * case the parent closes the dialog. The dialog never talks to the store: it converts,
   * shows the user what it made of the file, and hands the bytes over.
   */
  onImport(
    destination: ImportDestination,
    title: string,
    content: string,
  ): Promise<string | null>;
  onCancel(): void;
}

/**
 * Import a patent someone already wrote, from a `.txt`.
 *
 * **This is not the chat panel's drop zone**, and the two are kept visibly apart because
 * the same file type does two different jobs. Here the file BECOMES a patent; there it is
 * reference material the AI reads and the server never stores. The dialog says so in as
 * many words, and the chat zone says the converse.
 *
 * Conversion happens on the server, because the definition of "a claim" lives in the
 * Python parser and a second definition in TypeScript would drift from it. What comes
 * back is previewed — including every judgement the importer had to make — before
 * anything is created.
 */
export default function ImportPatentDialog({
  openPatentTitle,
  onImport,
  onCancel,
}: ImportPatentDialogProps) {
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<TextFile | null>(null);
  const [preview, setPreview] = useState<TextImportResult | null>(null);
  const [title, setTitle] = useState("");
  const [destination, setDestination] = useState<ImportDestination>("document");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);

  /** Read the file, then convert it. Both halves report failure in the same slot. */
  const accept = async (files: FileList | File[] | null) => {
    setError(null);
    const result = await readDroppedFiles(files, IMPORT_LIMIT);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setFile(result.file);
    setBusy(true);
    try {
      const converted = await importText(result.file.text, result.file.name);
      setPreview(converted);
      setTitle(converted.title);
    } catch (caught) {
      // A 413 or a 422 from the server is already a readable sentence; anything else
      // becomes one in `toMessage`. Never only `console.error`.
      setPreview(null);
      setError(toMessage(caught));
    } finally {
      setBusy(false);
    }
  };

  const submit = async () => {
    if (busy || !preview) return;
    const trimmed = title.trim();
    if (destination === "document" && !trimmed) {
      setError("A patent needs a title.");
      return;
    }
    setBusy(true);
    setError(null);
    const message = await onImport(destination, trimmed, preview.content);
    setBusy(false);
    if (message) setError(message);
  };

  return (
    <Modal
      labelledBy="import-patent-title"
      onDismiss={busy ? undefined : onCancel}
      className="max-w-[34rem] p-6"
    >
      <h2 id="import-patent-title" className="text-lg font-semibold tracking-tight">
        Import a patent from .txt
      </h2>
      <p className="mt-2 text-sm leading-relaxed text-slate-600">
        The file becomes a patent you can edit. To give the AI a file to <em>read</em>{" "}
        instead — prior art, say — drop it on the chat panel; that never changes your
        document.
      </p>

      <div
        data-testid="import-drop-zone"
        data-over={over ? "true" : "false"}
        onDragEnter={(event) => {
          event.preventDefault();
          setOver(true);
        }}
        onDragLeave={(event) => {
          event.preventDefault();
          setOver(false);
        }}
        // Without preventDefault on *dragover* specifically, `drop` never fires.
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault();
          setOver(false);
          if (!busy) void accept(event.dataTransfer?.files ?? null);
        }}
        className={`mt-5 rounded-lg border border-dashed px-3 py-4 text-center text-[0.8125rem] ${
          over ? "border-sky-400 bg-sky-50" : "border-slate-300 bg-slate-50"
        }`}
      >
        {file ? (
          <span className="truncate">
            📄 {file.name} · {formatBytes(file.bytes)}
          </span>
        ) : (
          <span className="text-slate-500">Drop a .txt patent here</span>
        )}
        {/* Drag-and-drop is unusable by keyboard, so the hidden input is the real
            accessible path — and it is also the one jsdom can drive. */}
        <input
          ref={input}
          type="file"
          accept=".txt"
          aria-label="Choose a .txt patent to import"
          className="sr-only"
          tabIndex={-1}
          onChange={async (event) => {
            // COPIED OUT FIRST. `event.target.files` is a LIVE FileList, so resetting
            // `value` empties the very list we are about to read and every pick arrives
            // as "no files". The array survives the reset; the FileList does not.
            const chosen = Array.from(event.target.files ?? []);
            event.target.value = ""; // so re-picking the SAME file fires change again
            await accept(chosen);
          }}
        />
        <div className="mt-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => input.current?.click()}
            className="btn btn-secondary focus-ring"
          >
            Choose a file
          </button>
        </div>
      </div>

      {preview && (
        <div className="mt-5">
          <p className="text-sm text-slate-700">
            Read <strong>{preview.claim_count}</strong>{" "}
            {preview.claim_count === 1 ? "claim" : "claims"}.
          </p>

          {/* The "never fail silently" half. Every judgement the importer had to make is
              on screen BEFORE the patent exists, so an odd claim set is the user's
              decision rather than a surprise they find later. */}
          {preview.notes.length > 0 && (
            <ul
              role="status"
              className="mt-2 space-y-1 rounded-lg bg-amber-50 px-3 py-2 text-[0.8125rem] text-amber-900 ring-1 ring-amber-100"
            >
              {preview.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}

          {openPatentTitle !== null && (
            <fieldset className="mt-4">
              <legend className="text-sm font-medium">Import as</legend>
              <label className="mt-1 flex items-center gap-2 text-sm">
                <input
                  type="radio"
                  name="import-destination"
                  checked={destination === "document"}
                  onChange={() => setDestination("document")}
                />
                A new patent
              </label>
              <label className="mt-1 flex items-center gap-2 text-sm">
                <input
                  type="radio"
                  name="import-destination"
                  checked={destination === "version"}
                  onChange={() => setDestination("version")}
                />
                A new version of “{openPatentTitle}”
              </label>
            </fieldset>
          )}

          {destination === "document" && (
            <>
              <label htmlFor="import-title-input" className="mt-4 block text-sm font-medium">
                Title
              </label>
              <input
                id="import-title-input"
                required
                maxLength={200}
                value={title}
                disabled={busy}
                onChange={(event) => setTitle(event.target.value)}
                className="focus-ring mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm disabled:opacity-60"
              />
            </>
          )}
        </div>
      )}

      {error && (
        <p
          role="alert"
          className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 ring-1 ring-red-100"
        >
          {error}
        </p>
      )}

      <div className="mt-6 flex justify-end gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={onCancel}
          className="btn btn-secondary focus-ring"
        >
          Cancel
        </button>
        <button
          type="button"
          aria-label="Import patent"
          disabled={busy || !preview}
          onClick={() => void submit()}
          className="btn btn-primary focus-ring"
        >
          <SpinnerLabel spinning={busy}>Import</SpinnerLabel>
        </button>
      </div>
    </Modal>
  );
}
