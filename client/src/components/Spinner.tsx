export interface SpinnerProps {
  /** Size only — it is used standalone and inside a button, at two sizes. */
  className?: string;
}

export default function Spinner({ className = "h-9 w-9" }: SpinnerProps) {
  return (
    <div
      role="status"
      aria-label="Loading"
      className={`animate-spin rounded-full border-2 border-slate-200 border-b-sky-500 ${className}`}
    />
  );
}
