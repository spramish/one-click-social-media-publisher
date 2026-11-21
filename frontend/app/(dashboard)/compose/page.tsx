import type { Metadata } from "next";

import { PageHeader } from "@/components/page-header";
import { PublisherComposer } from "@/components/posts/composer";

export const metadata: Metadata = { title: "Composer" };

export default function ComposePage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Publishing composer"
        description="Pick a video, write per-platform captions, and publish to every destination at once."
      />
      <PublisherComposer />
    </div>
  );
}
