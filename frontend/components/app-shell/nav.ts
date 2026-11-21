import {
  BarChart3,
  CalendarClock,
  Cpu,
  History,
  LayoutDashboard,
  PenSquare,
  Settings,
  Share2,
  Sparkles,
  UploadCloud,
  type LucideIcon,
} from "lucide-react";

export type NavItem = {
  title: string;
  href: string;
  icon: LucideIcon;
  /** Permission code from the active workspace's role. Omit = visible to all members. */
  permission?: string;
};

export type NavSection = {
  label: string;
  items: NavItem[];
};

export const NAV_SECTIONS: NavSection[] = [
  {
    label: "Overview",
    items: [
      { title: "Dashboard", href: "/dashboard", icon: LayoutDashboard },
      { title: "Publishing History", href: "/history", icon: History },
    ],
  },
  {
    label: "Create",
    items: [
      { title: "Upload Video", href: "/upload", icon: UploadCloud },
      { title: "Composer", href: "/compose", icon: PenSquare },
      { title: "Schedule", href: "/schedule", icon: CalendarClock },
      { title: "AI Assistant", href: "/assistant", icon: Sparkles },
    ],
  },
  {
    label: "Manage",
    items: [
      { title: "Social Accounts", href: "/accounts", icon: Share2 },
      { title: "Analytics", href: "/analytics", icon: BarChart3 },
      { title: "Settings", href: "/settings", icon: Settings },
      { title: "AI Models", href: "/ai-models", icon: Cpu, permission: "ai:manage" },
    ],
  },
];
