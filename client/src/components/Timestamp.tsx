import { absoluteUtc, machineStamp, relativeTime } from "./time";

export interface TimestampProps {
  /** Naive UTC from the server, e.g. "2026-01-01T09:30:00". */
  stamp: string;
}

/**
 * Relative text, exact UTC on hover. A real <time> element so the machine-readable
 * instant is in the markup and not only in the tooltip.
 *
 * It carries no styling of its own: both call sites render it inside a line that
 * has already set the size and colour, so an inherited style is the correct one.
 */
export default function Timestamp({ stamp }: TimestampProps) {
  return (
    <time dateTime={machineStamp(stamp)} title={absoluteUtc(stamp)}>
      {relativeTime(stamp)}
    </time>
  );
}
