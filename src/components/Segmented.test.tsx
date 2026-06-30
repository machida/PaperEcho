import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Segmented } from "./Segmented";

describe("Segmented", () => {
  it("renders an option per entry and marks the selected one", () => {
    render(
      <Segmented options={[0.5, 1, 2]} value={1} onChange={() => {}} label={(m) => `${m}x`} />,
    );
    const selected = screen.getByRole("button", { name: "1x" });
    expect(selected).toHaveClass("on");
    expect(screen.getByRole("button", { name: "0.5x" })).not.toHaveClass("on");
    expect(screen.getAllByRole("button")).toHaveLength(3);
  });

  it("calls onChange with the clicked option", async () => {
    const onChange = vi.fn();
    render(<Segmented options={["fixed", "variable"]} value="fixed" onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: "variable" }));
    expect(onChange).toHaveBeenCalledExactlyOnceWith("variable");
  });

  it("falls back to the raw value when no label is given", () => {
    render(<Segmented options={[-1, 0, 1]} value={0} onChange={() => {}} />);
    expect(screen.getByRole("button", { name: "-1" })).toBeInTheDocument();
  });
});
