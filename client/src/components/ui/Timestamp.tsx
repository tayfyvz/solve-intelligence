import { absoluteUtc, machineStamp, relativeTime } from "../../utils/time";

export interface TimestampProps {
  /** Naive UTC from the server, e.g. "2026-01-01T09:30:00". */
  stamp: string;
}

/** Relative text, exact UTC on hover. A real <time> element, so the machine-readable instant
 *  is in the markup. No styling of its own: the call sites set size and colour. */
export default function Timestamp({ stamp }: TimestampProps) {
  return (
    <time dateTime={machineStamp(stamp)} title={absoluteUtc(stamp)}>
      {relativeTime(stamp)}
    </time>
  );
}
