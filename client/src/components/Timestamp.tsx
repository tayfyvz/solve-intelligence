import { absoluteUtc, machineStamp, relativeTime } from "./time";

export interface TimestampProps {
  /** Naive UTC from the server, e.g. "2026-01-01T09:30:00". */
  stamp: string;
  className?: string;
}

/**
 * Relative text, exact UTC on hover. A real <time> element so the machine-readable
 * instant is in the markup and not only in the tooltip.
 */
export default function Timestamp({ stamp, className }: TimestampProps) {
  return (
    <time dateTime={machineStamp(stamp)} title={absoluteUtc(stamp)} className={className}>
      {relativeTime(stamp)}
    </time>
  );
}
