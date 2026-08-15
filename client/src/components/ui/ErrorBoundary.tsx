import { Component, type ErrorInfo, type ReactNode } from "react";

export interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  message: string | null;
}

/**
 * The last line of "never fail silently": every async failure has a rendered error state,
 * but a throw during render unmounts the tree and leaves a white page.
 *
 * A class, because `componentDidCatch` has no hook equivalent. It does not try to recover —
 * the tree that threw is gone, and with it any unsaved draft — so it offers a reload.
 */
export default class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { message: null };

  static getDerivedStateFromError(error: unknown): ErrorBoundaryState {
    return { message: error instanceof Error && error.message ? error.message : "Something went wrong." };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // The panel below is the user-facing report; this is where the stack is worth having.
    console.error("Unhandled render error", error, info.componentStack);
  }

  render(): ReactNode {
    if (this.state.message === null) return this.props.children;
    return (
      <div role="alert" className="flex h-full w-full items-center justify-center p-6">
        <div className="max-w-md rounded-xl border border-red-200 bg-red-50 p-6 text-sm text-red-800">
          <h1 className="text-base font-semibold">Something went wrong.</h1>
          <p className="mt-2 leading-relaxed">
            The page could not be displayed. Any unsaved changes in the editor have been lost.
          </p>
          <p className="mt-2 font-mono text-xs text-red-700">{this.state.message}</p>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="btn btn-primary focus-ring mt-4"
          >
            Reload the page
          </button>
        </div>
      </div>
    );
  }
}
