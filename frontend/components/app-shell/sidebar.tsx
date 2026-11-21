"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { X } from "lucide-react";

import { NAV_SECTIONS } from "@/components/app-shell/nav";
import { Brand } from "@/components/brand";
import { useAuth } from "@/lib/auth-context";
import { cn } from "@/lib/utils";

export function Sidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
  const pathname = usePathname();
  const { hasPermission } = useAuth();

  return (
    <>
      {open ? (
        <div
          className="fixed inset-0 z-40 bg-black/50 lg:hidden"
          onClick={onClose}
          aria-hidden
        />
      ) : null}

      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-50 flex w-64 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground transition-transform duration-200 lg:translate-x-0",
          open ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex h-16 items-center justify-between px-5">
          <Link href="/dashboard" onClick={onClose} aria-label="One-Click Publisher home">
            <Brand />
          </Link>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1.5 text-sidebar-muted hover:bg-sidebar-accent lg:hidden"
            aria-label="Close navigation"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <nav className="flex-1 space-y-6 overflow-y-auto px-3 py-4" aria-label="Primary">
          {NAV_SECTIONS.map((section) => (
            <div key={section.label}>
              <p className="px-3 pb-2 text-[11px] font-semibold uppercase tracking-wider text-sidebar-muted">
                {section.label}
              </p>
              <ul className="space-y-1">
                {section.items.map((item) => {
                  const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
                  const allowed = !item.permission || hasPermission(item.permission);
                  return (
                    <li key={item.href}>
                      <Link
                        href={item.href}
                        onClick={onClose}
                        aria-current={active ? "page" : undefined}
                        className={cn(
                          "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                          active
                            ? "bg-sidebar-accent text-sidebar-accent-foreground"
                            : "text-sidebar-muted hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground",
                        )}
                      >
                        <item.icon className="h-4 w-4 shrink-0" />
                        <span className="flex-1 truncate">{item.title}</span>
                        {!allowed ? (
                          <span className="text-[10px] uppercase tracking-wide text-sidebar-muted/70">
                            locked
                          </span>
                        ) : null}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </nav>

        <div className="border-t border-sidebar-border p-4">
          <p className="text-xs text-sidebar-muted">
            <span className="font-medium text-sidebar-foreground">Phase 4</span> · Frontend dashboard
          </p>
          <p className="mt-1 text-[11px] text-sidebar-muted/80">Author: Pramish Sharma</p>
        </div>
      </aside>
    </>
  );
}
