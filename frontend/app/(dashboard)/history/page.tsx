import type { Metadata } from "next";

import { ModulePlaceholder } from "@/components/module-placeholder";
import { PageHeader } from "@/components/page-header";

export const metadata: Metadata = { title: "Publishing history" };

export default function HistoryPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Publishing history"
        description="Every publishing attempt, with its per-platform result."
      />
      <ModulePlaceholder
        phase={7}
        title="Publishing history"
        description="History is populated by the publishing engine in Phase 7. The table stays empty until a first post is published."
        capabilities={[
          "Chronological log of every publishing attempt",
          "Per-platform status (queued, processing, published, failed)",
          "Returned post identifiers and public links",
          "Stored platform response and error detail",
          "Retry an individual failed destination",
          "Filter by platform, status, and date",
          "Search titles and captions",
          "Export the history as CSV",
        ]}
      />
    </div>
  );
}
