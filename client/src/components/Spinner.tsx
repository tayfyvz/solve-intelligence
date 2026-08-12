export default function Spinner() {
  return (
    <div
      role="status"
      aria-label="Loading"
      className="h-9 w-9 animate-spin rounded-full border-2 border-slate-200 border-b-sky-500"
    />
  );
}
