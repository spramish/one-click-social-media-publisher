import { render, screen } from "@testing-library/react";
import { Inbox } from "lucide-react";
import { describe, expect, it } from "vitest";

import { EmptyState } from "@/components/empty-state";

describe("EmptyState", () => {
  it("renders the title and description", () => {
    render(<EmptyState icon={Inbox} title="No videos yet" description="Upload one to begin." />);

    expect(screen.getByRole("heading", { name: "No videos yet" })).toBeInTheDocument();
    expect(screen.getByText("Upload one to begin.")).toBeInTheDocument();
  });

  it("renders a call to action only when one is supplied", () => {
    const { rerender } = render(
      <EmptyState icon={Inbox} title="Empty" description="Nothing here." />,
    );
    expect(screen.queryByRole("link")).not.toBeInTheDocument();

    rerender(
      <EmptyState
        icon={Inbox}
        title="Empty"
        description="Nothing here."
        action={<a href="/upload">Upload</a>}
      />,
    );
    expect(screen.getByRole("link", { name: "Upload" })).toHaveAttribute("href", "/upload");
  });
});
