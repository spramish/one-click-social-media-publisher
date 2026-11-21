"use client";

import * as React from "react";

import { Sidebar } from "@/components/app-shell/sidebar";
import { Topbar } from "@/components/app-shell/topbar";
import { RequireAuth } from "@/components/require-auth";

/** The authenticated application frame: sidebar + topbar + content area. */
export function DashboardShell({ children }: { children: React.ReactNode }) {
  const [navOpen, setNavOpen] = React.useState(false);

  return (
    <RequireAuth>
      <div className="min-h-screen bg-background">
        <Sidebar open={navOpen} onClose={() => setNavOpen(false)} />
        <div className="lg:pl-64">
          <Topbar onMenuClick={() => setNavOpen(true)} />
          <main className="mx-auto w-full max-w-7xl px-4 py-6 lg:px-8 lg:py-8">{children}</main>
        </div>
      </div>
    </RequireAuth>
  );
}
