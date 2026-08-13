import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import VersionBar, { type VersionBarProps } from "../components/VersionBar";

/**
 * VersionBar is props-only, so it is testable without the store. These two tests
 * cover what App-level tests cannot see: the timestamp formatting, and which of
 * the two buttons spins.
 */
function barProps(overrides: Partial<VersionBarProps> = {}): VersionBarProps {
  return {
    title: "Patent 1",
    versions: [
      { version_number: 1, updated_at: "2026-01-01T09:30:00" },
      // Date-only: what a fixture or a differently-serialising server can send.
      { version_number: 2, updated_at: "2026-04-04" },
    ],
    selected: 1,
    dirty: false,
    busy: false,
    pendingAction: null,
    onSelectVersion: vi.fn(),
    onSave: vi.fn(),
    onSaveAsNewVersion: vi.fn(),
    ...overrides,
  };
}

describe("VersionBar", () => {
  // Naive UTC shown verbatim and labelled, trimmed to the minute. The date-only
  // case is why `formatSaved` builds by cases: trimming would leave a double
  // space before "UTC".
  it("shows the selected version's saved time, trimmed to the minute", () => {
    const { rerender } = render(<VersionBar {...barProps()} />);
    expect(screen.getByText("saved 2026-01-01 09:30 UTC")).toBeTruthy();

    rerender(<VersionBar {...barProps({ selected: 2 })} />);
    expect(screen.getByText("saved 2026-04-04 UTC")).toBeTruthy();
  });

  // Only the pressed button spins — `busy` alone is true for both, so a spinner
  // driven by it would put one on the button the user did not press. The
  // accessible names must survive: the spinner is a labelled status node, and
  // without the buttons' own aria-label it would rename them "Loading".
  it("spins only the write that is in flight", () => {
    render(<VersionBar {...barProps({ busy: true, pendingAction: "saveAsNew" })} />);

    const save = screen.getByRole("button", { name: "Save" });
    const saveAsNew = screen.getByRole("button", { name: "Save as new version" });

    expect(within(saveAsNew).getByRole("status", { name: "Loading" })).toBeTruthy();
    expect(within(save).queryByRole("status")).toBeNull();
    expect((save as HTMLButtonElement).disabled).toBe(true);
  });
});
