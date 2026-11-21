import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Button } from "@/components/ui/button";

describe("Button", () => {
  it("renders a native button by default", () => {
    render(<Button>Save</Button>);
    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
  });

  it("applies the requested variant and size classes", () => {
    render(
      <Button variant="destructive" size="lg">
        Delete
      </Button>,
    );
    const button = screen.getByRole("button", { name: "Delete" });
    expect(button.className).toContain("bg-destructive");
    expect(button.className).toContain("h-11");
  });

  it("forwards the disabled state", () => {
    render(<Button disabled>Wait</Button>);
    expect(screen.getByRole("button", { name: "Wait" })).toBeDisabled();
  });

  it("renders as its child element when asChild is set", () => {
    render(
      <Button asChild>
        <a href="/dashboard">Open dashboard</a>
      </Button>,
    );
    const link = screen.getByRole("link", { name: "Open dashboard" });
    expect(link).toHaveAttribute("href", "/dashboard");
  });
});
