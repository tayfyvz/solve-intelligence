import { useRef, useState } from "react";

import {
  formatBytes,
  readDroppedFiles,
  type ContextFile,
  type ContextFileResult,
} from "../contextFile";

export interface TxtDropZoneProps {
  file: ContextFile | null;
  onAttach(file: ContextFile): void;
  /** The zone never renders its own error — the transcript owns error display. */
  onReject(message: string): void;
  onClear(): void;
  /** True while a request is in flight: the zone stops accepting, the chip stays visible. */
  disabled: boolean;
}

/**
 * Drop target and keyboard path for one `.txt` of context.
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

  const deliver = (result: ContextFileResult) =>
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
      className={`flex items-center justify-between gap-2 rounded-lg border border-dashed px-3 py-2 text-[0.8125rem] ${
        over ? "border-sky-400 bg-sky-50" : "border-slate-300 bg-white"
      }`}
    >
      {file ? (
        <span className="flex min-w-0 items-center gap-2">
          <span className="truncate">
            📄 {file.name} · {formatBytes(file.bytes)}
          </span>
          <button
            type="button"
            aria-label={`Remove ${file.name}`}
            onClick={onClear}
            className="shrink-0 rounded px-1 text-slate-500 hover:bg-slate-100 hover:text-slate-900"
          >
            ✕
          </button>
        </span>
      ) : (
        <span className="truncate text-slate-500">Drop a .txt for context</span>
      )}

      {/* Drag-and-drop is unusable by keyboard, so the hidden input is the real
          accessible path — and it is also the one jsdom can drive. */}
      <input
        ref={input}
        type="file"
        accept=".txt"
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
        className="shrink-0 rounded border border-slate-300 px-2 py-1 font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-50"
      >
        Attach .txt
      </button>
    </div>
  );
}
