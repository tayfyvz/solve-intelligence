import { useRef, useState } from "react";

import {
  formatBytes,
  readDroppedFiles,
  type TextFile,
  type TextFileResult,
} from "../textFile";

export interface TxtDropZoneProps {
  file: TextFile | null;
  onAttach(file: TextFile): void;
  /** The zone never renders its own error — the transcript owns error display. */
  onReject(message: string): void;
  onClear(): void;
  /** True while a request is in flight: the zone stops accepting, the chip stays visible. */
  disabled: boolean;
}

/**
 * Drop target and keyboard path for one `.txt` of context.
 *
 * The copy says "for the AI to read" rather than "for context", because the same file type
 * now has a second, opposite job: `ImportPatentDialog` turns a `.txt` INTO a patent. This
 * one never changes the document, so the zone says what it does instead of naming a concept.
 *
 * Both paths funnel into `readDroppedFiles`, so there is one rule set rather than
 * two that can drift. The zone renders no error itself: rejections go to the
 * transcript, which is already a live region.
 */
export default function TxtDropZone({
  file,
  onAttach,
  onReject,
  onClear,
  disabled,
}: TxtDropZoneProps) {
  const input = useRef<HTMLInputElement>(null);
  // A counter, not a boolean: `dragleave` fires every time the pointer crosses onto
  // a child element, so a boolean flickers the highlight continuously as the cursor
  // moves across the chip and the button inside the zone.
  const depth = useRef(0);
  const [over, setOver] = useState(false);

  const deliver = (result: TextFileResult) =>
    result.ok ? onAttach(result.file) : onReject(result.error);

  const onDragEnter = (e: React.DragEvent) => {
    e.preventDefault();
    depth.current += 1;
    setOver(true);
  };

  const onDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    depth.current -= 1;
    if (depth.current <= 0) {
      depth.current = 0;
      setOver(false);
    }
  };

  const onDragOver = (e: React.DragEvent) => {
    // WITHOUT preventDefault on *dragover* specifically, `drop` never fires. The
    // classic bug — preventing it on dragenter alone is not enough.
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
  };

  const onDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    depth.current = 0; // NOT -= 1: a drop can arrive with unbalanced enter/leave counts
    setOver(false); // after a drag across a re-rendering child, and a stuck positive
    if (disabled) return; // count leaves the zone permanently highlighted.
    deliver(await readDroppedFiles(e.dataTransfer?.files ?? null));
  };

  return (
    <div
      data-testid="txt-drop-zone"
      // The highlight is styling; this attribute is the fact, so a test can read it
      // without depending on a class name.
      data-over={over ? "true" : "false"}
      onDragEnter={onDragEnter}
      onDragLeave={onDragLeave}
      onDragOver={onDragOver}
      onDrop={onDrop}
      className={`flex items-center gap-2 rounded-lg border border-dashed px-3 py-1.5 text-[0.8125rem] transition-colors ${
        over ? "border-sky-400 bg-sky-50" : "border-slate-300 bg-slate-50"
      }`}
    >
      {file ? (
        <span className="flex min-w-0 flex-1 items-center gap-1.5 text-slate-700">
          <FileIcon className="h-4 w-4 shrink-0 text-slate-500" />
          <span className="truncate">
            <span className="font-medium">{file.name}</span>{" "}
            <span className="text-slate-500">· {formatBytes(file.bytes)}</span>
          </span>
          <button
            type="button"
            aria-label={`Remove ${file.name}`}
            onClick={onClear}
            className="focus-ring ml-auto shrink-0 rounded-md p-1 text-slate-500 hover:bg-slate-200/60 hover:text-slate-900"
          >
            <CloseIcon className="h-3 w-3" />
          </button>
        </span>
      ) : (
        // Empty: no label next to the button. The button already says "Attach
        // .txt"; a copy of that same fact in prose next to it was noise, not
        // information — this flex-1 spacer is what keeps the button pinned to
        // the right edge of the zone rather than collapsing to its own width.
        <span className="min-w-0 flex-1" />
      )}

      {/* Drag-and-drop is unusable by keyboard, so the hidden input is the real
          accessible path — and it is also the one jsdom can drive. */}
      <input
        ref={input}
        type="file"
        accept=".txt"
        // Named here rather than with a <label>: the visible trigger is the button
        // below, so a label would be a second, duplicate control in the tab order.
        aria-label="Attach a .txt file for context"
        className="sr-only"
        tabIndex={-1}
        onChange={async (e) => {
          const result = await readDroppedFiles(e.target.files);
          e.target.value = ""; // so re-picking the SAME file fires change again
          deliver(result);
        }}
      />
      <button
        type="button"
        disabled={disabled}
        onClick={() => input.current?.click()}
        className="btn btn-secondary focus-ring shrink-0 px-2.5 text-[0.75rem]"
      >
        Attach .txt
      </button>
    </div>
  );
}

/** Minimal document-with-folded-corner glyph, matching the house icon style (see the
 * pencil in TreeRow.tsx): 20x20 viewBox, currentColor, one path, aria-hidden because
 * the surrounding text or button already names the control. */
function FileIcon({ className }: { className: string }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20" fill="currentColor" className={className}>
      <path d="M6 2a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7.828a2 2 0 0 0-.586-1.414l-3.828-3.828A2 2 0 0 0 10.172 2H6Zm4 1.5V6a1 1 0 0 0 1 1h2.5L10 3.5Z" />
    </svg>
  );
}

function CloseIcon({ className }: { className: string }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20" fill="currentColor" className={className}>
      <path d="M4.3 4.3a1 1 0 0 1 1.4 0L10 8.6l4.3-4.3a1 1 0 1 1 1.4 1.4L11.4 10l4.3 4.3a1 1 0 0 1-1.4 1.4L10 11.4l-4.3 4.3a1 1 0 0 1-1.4-1.4L8.6 10 4.3 5.7a1 1 0 0 1 0-1.4Z" />
    </svg>
  );
}
