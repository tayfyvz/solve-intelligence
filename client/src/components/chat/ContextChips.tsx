import type { TextFile } from "../../textFile";
import TxtDropZone from "../TxtDropZone";

export interface ContextChipsProps {
  /** `Selection · claim 3` / `Selection · claims 3–5` / `Selection · 412 characters`. */
  selectionLabel: string | null;
  onClearSelection(): void;
  /** Hovering the chip paints the range in the document; leaving clears it. */
  onHoverSelection(hovering: boolean): void;
  file: TextFile | null;
  onAttach(file: TextFile): void;
  onReject(message: string): void;
  onClearFile(): void;
  disabled: boolean;
}

/**
 * The two pieces of read-only context, above the composer. The file half is
 * `TxtDropZone` (5A) rather than a second chip implementation — one rule set for
 * attaching, one chip, one ✕.
 */
export default function ContextChips({
  selectionLabel,
  onClearSelection,
  onHoverSelection,
  file,
  onAttach,
  onReject,
  onClearFile,
  disabled,
}: ContextChipsProps) {
  return (
    <>
      {selectionLabel && (
        <span
          onMouseEnter={() => onHoverSelection(true)}
          onMouseLeave={() => onHoverSelection(false)}
          className="flex items-center justify-between gap-2 rounded-lg border border-sky-200 bg-sky-50 px-3 py-1.5 text-[0.8125rem] text-sky-900"
        >
          <span className="truncate">{selectionLabel}</span>
          <button
            type="button"
            aria-label="Clear selection context"
            onClick={onClearSelection}
            className="focus-ring shrink-0 rounded px-1 text-sky-700 hover:bg-sky-100"
          >
            ✕
          </button>
        </span>
      )}

      <TxtDropZone
        file={file}
        onAttach={onAttach}
        onReject={onReject}
        onClear={onClearFile}
        disabled={disabled}
      />
    </>
  );
}
