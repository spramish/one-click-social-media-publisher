import type { Metadata } from "next";

import { AnalyticsDashboard } from "@/components/analytics/analytics-dashboard";
import { PageHeader } from "@/components/page-header";

export const metadata: Metadata = { title: "Analytics" };

export default function AnalyticsPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Analytics"
        description="See how published videos perform across platforms. Figures come straight from each platform's API — anything it does not report is shown as N/A, never as zero."
      />
      <AnalyticsDashboard />
    </div>
  );
}
