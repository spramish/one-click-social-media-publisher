import type { ReactNode } from "react";

import { DashboardShell } from "@/components/app-shell/dashboard-shell";

export default function DashboardGroupLayout({ children }: { children: ReactNode }) {
  return <DashboardShell>{children}</DashboardShell>;
}
