import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import ErrorBoundary from "../components/ErrorBoundary";

function Explodes(): never {
  throw new Error("Cannot read properties of null");
}

// Every async failure in this app already renders a sentence; a throw during
// RENDER did not. It unmounted the whole tree and left a white page with nothing
// to read and no way back.
it("renders the message and a reload button instead of a white page", () => {
  // React itself logs the caught error, and the boundary logs the component
  // trace. Both are wanted in a real session and neither is wanted here.
  const logged = vi.spyOn(console, "error").mockImplementation(() => {});
  try {
    render(
      <ErrorBoundary>
        <Explodes />
      </ErrorBoundary>,
    );

    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByText("Cannot read properties of null")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reload the page" })).toBeTruthy();
  } finally {
    logged.mockRestore();
  }
});

it("renders its children untouched when nothing throws", () => {
  render(
    <ErrorBoundary>
      <p>the app</p>
    </ErrorBoundary>,
  );

  expect(screen.getByText("the app")).toBeTruthy();
  expect(screen.queryByRole("alert")).toBeNull();
});
