import { type PointerEvent as ReactPointerEvent, type ReactNode } from "react";

/** Narrow enough to still read a patent title, wide enough not to eat the editor. */
const MIN_WIDTH = 200;
const MAX_WIDTH = 520;
/** One arrow press. Big enough to be worth pressing, small enough to be precise. */
const STEP = 24;

export interface SidePanelProps {
  /** Which edge the resize handle lives on, and which way a drag widens it. */
  side: "left" | "right";
  /** Names the panel in the collapse button and the rail, e.g. "Patents". */
  label: string;
  collapsed: boolean;
  onCollapsedChange(collapsed: boolean): void;
  width: number;
  onWidthChange(width: number): void;
  /**
   * False below the `lg` breakpoint, where the columns stack: a pixel width there
   * would leave a 240px panel floating in a full-width row.
   */
  resizable: boolean;
  children: ReactNode;
}

const clamp = (width: number) => Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, width));

/**
 * A collapsible, drag-resizable column. Deliberately a pointer handler and a
 * number in the parent's state rather than a resizable-panel dependency —
 * this is thirty lines and no new package to explain in a pair session.
 */
export default function SidePanel({
  side,
  label,
  collapsed,
  onCollapsedChange,
  width,
  onWidthChange,
  resizable,
  children,
}: SidePanelProps) {
  // Pointer capture keeps the move/up events coming to the handle even when the
  // cursor outruns it, which is why there are no window listeners here and no
  // "am I dragging" flag to leak.
  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const handle = event.currentTarget;
    handle.setPointerCapture(event.pointerId);
    const startX = event.clientX;
    const startWidth = width;

    const onMove = (move: PointerEvent) => {
      const delta = move.clientX - startX;
      onWidthChange(clamp(side === "left" ? startWidth + delta : startWidth - delta));
    };
    const onUp = () => {
      handle.removeEventListener("pointermove", onMove);
      handle.removeEventListener("pointerup", onUp);
      handle.removeEventListener("pointercancel", onUp);
      handle.removeEventListener("lostpointercapture", onUp);
    };
    handle.addEventListener("pointermove", onMove);
    handle.addEventListener("pointerup", onUp);
    handle.addEventListener("pointercancel", onUp);
    // The capture can be lost without a pointerup — the element is removed, or
    // another element captures. Without this the listeners stay bound to a closure
    // holding a stale `startWidth`, and the next drag jumps by the old delta.
    handle.addEventListener("lostpointercapture", onUp);
  };

  if (collapsed) {
    return (
      <div className="flex shrink-0 lg:w-9">
        <button
          type="button"
          aria-expanded={false}
          aria-label={`Expand ${label} panel`}
          onClick={() => onCollapsedChange(false)}
          className="focus-ring flex w-full items-center justify-center gap-2 rounded-xl border border-slate-200 bg-white px-2 py-2 text-slate-500 transition-colors duration-150 hover:bg-slate-50 lg:flex-col lg:py-4"
        >
          <span aria-hidden="true" className="text-xs">
            {side === "left" ? "›" : "‹"}
          </span>
          {/* Vertical only where the rail is a column; stacked, it reads across. */}
          <span className="text-xs font-semibold uppercase tracking-wide lg:[writing-mode:vertical-rl]">
            {label}
          </span>
        </button>
      </div>
    );
  }

  return (
    <div
      // The pixel width applies only where the layout is columns; below `lg` the
      // panel is a full-width block and this is undefined.
      style={resizable ? { width } : undefined}
      className={`flex min-h-0 shrink-0 ${side === "left" ? "flex-row" : "flex-row-reverse"} max-lg:w-full`}
    >
      <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-1.5 overflow-hidden rounded-xl border border-slate-200 bg-white p-2.5 shadow-sm">
        <div className={`flex ${side === "left" ? "justify-end" : "justify-start"}`}>
          <button
            type="button"
            aria-expanded={true}
            aria-label={`Collapse ${label} panel`}
            onClick={() => onCollapsedChange(true)}
            className="focus-ring flex h-5 w-5 items-center justify-center rounded-md text-slate-500 transition-colors duration-150 hover:bg-slate-100 hover:text-slate-700"
          >
            <span aria-hidden="true" className="text-xs">
              {side === "left" ? "‹" : "›"}
            </span>
          </button>
        </div>
        {children}
      </div>

      {/* A real separator: draggable with a pointer, nudgeable with the arrow keys,
          and hidden below `lg` where there is nothing to resize. */}
      <div
        role="separator"
        tabIndex={0}
        aria-orientation="vertical"
        aria-label={`Resize ${label} panel`}
        aria-valuenow={width}
        aria-valuemin={MIN_WIDTH}
        aria-valuemax={MAX_WIDTH}
        onPointerDown={onPointerDown}
        onKeyDown={(event) => {
          const direction =
            event.key === "ArrowLeft" ? -1 : event.key === "ArrowRight" ? 1 : 0;
          if (!direction) return;
          event.preventDefault();
          onWidthChange(clamp(width + direction * STEP * (side === "left" ? 1 : -1)));
        }}
        className="focus-ring mx-0.5 hidden w-1.5 shrink-0 cursor-col-resize rounded-full bg-transparent transition-colors duration-150 hover:bg-slate-300 lg:block"
      />
    </div>
  );
}
