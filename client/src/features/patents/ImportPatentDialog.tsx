import { useRef, useState } from "react";

import { importText, toMessage } from "../../services/api";
import { IMPORT_LIMIT, formatBytes, readDroppedFiles, type TextFile } from "../../utils/textFile";
import type { TextImportResult } from "../../types";
import Modal from "../../components/ui/Modal";
import { SpinnerLabel } from "../../components/ui/Spinner";

export type ImportDestination = "document" | "version";

export interface ImportPatentDialogProps {
  /** The open patent's title, or null when nothing is open — which is also the only
   *  thing that decides whether "new version" is offered at all. */
  openPatentTitle: string | null;
  /** Commits the conversion: resolves to an error sentence, or null on success. The dialog
   *  never talks to the store — it converts, previews, and hands the bytes over. */
  onImport(
    destination: ImportDestination,
    title: string,
    content: string,
  ): Promise<string | null>;
  onCancel(): void;
}

/**
 * Import a patent someone already wrote, from a `.txt`. Not the chat panel's drop zone: the
 * same file type does two different jobs, and here the file *becomes* a patent, so both
 * surfaces say which one the user is on.
 *
 * Conversion happens on the server, because the definition of "a claim" lives in the Python
 * parser and a second definition in TypeScript would drift from it. The result — including
 * every judgement the importer made — is previewed before anything is created.
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
      // A 413 or 422 is already a readable sentence; anything else becomes one in
      // `toMessage`. Never only `console.error`.
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
        {/* Drag-and-drop is unusable by keyboard, so the hidden input is the accessible
            path — and the one jsdom can drive. */}
        <input
          ref={input}
          type="file"
          accept=".txt"
          aria-label="Choose a .txt patent to import"
          className="sr-only"
          tabIndex={-1}
          onChange={async (event) => {
            // Copied out FIRST: `event.target.files` is a live FileList, so resetting
            // `value` empties the list we are about to read. The array survives it.
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

          {/* Every judgement the importer made is on screen before the patent exists, so an
              odd claim set is the user's decision, not a surprise found later. */}
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
