import type { Metadata } from "next";

import { PageHeader } from "@/components/page-header";
import { SchedulingCalendar } from "@/components/schedule/scheduling-calendar";

export const metadata: Metadata = { title: "Schedule" };

export default function SchedulePage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Scheduling calendar"
        description="Queue posts for a future time, review what is coming up, and manage what has already fired."
      />
      <SchedulingCalendar />
    </div>
  );
}
