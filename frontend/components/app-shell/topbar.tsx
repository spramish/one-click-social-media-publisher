"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import {
  Check,
  ChevronsUpDown,
  Cpu,
  LogOut,
  Menu,
  Moon,
  Settings,
  ShieldAlert,
  Sun,
} from "lucide-react";

import { Avatar, AvatarFallback, initialsOf } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { NotificationBell } from "@/components/notifications/notification-bell";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { toast } from "@/hooks/use-toast";
import { useAuth } from "@/lib/auth-context";
import { useTheme } from "@/lib/theme-context";
import { cn } from "@/lib/utils";

export function Topbar({ onMenuClick }: { onMenuClick: () => void }) {
  const router = useRouter();
  const { user, workspaces, activeWorkspace, setActiveWorkspace, logout, logoutAll } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const [signingOut, setSigningOut] = useState(false);

  async function handleSignOut(everywhere: boolean) {
    setSigningOut(true);
    try {
      if (everywhere) await logoutAll();
      else await logout();
      toast({ title: "Signed out", variant: "success" });
      router.replace("/login");
    } catch {
      toast({ title: "Could not sign out", variant: "destructive" });
    } finally {
      setSigningOut(false);
    }
  }

  return (
    <header className="sticky top-0 z-30 flex h-16 items-center gap-3 border-b bg-background/80 px-4 backdrop-blur lg:px-6">
      <button
        type="button"
        onClick={onMenuClick}
        className="rounded-md p-2 text-muted-foreground hover:bg-accent hover:text-accent-foreground lg:hidden"
        aria-label="Open navigation"
      >
        <Menu className="h-5 w-5" />
      </button>

      {/* Workspace switcher */}
      <DropdownMenu>
        <DropdownMenuTrigger className="flex items-center gap-2 rounded-md border bg-card px-3 py-2 text-sm font-medium transition-colors hover:bg-accent">
          <span className="flex h-6 w-6 items-center justify-center rounded bg-primary/10 text-[11px] font-semibold text-primary">
            {initialsOf(activeWorkspace?.name ?? "W")}
          </span>
          <span className="max-w-[10rem] truncate">{activeWorkspace?.name ?? "Workspace"}</span>
          <ChevronsUpDown className="h-3.5 w-3.5 text-muted-foreground" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-64">
          <DropdownMenuLabel>Switch workspace</DropdownMenuLabel>
          {workspaces.map((workspace) => (
            <DropdownMenuItem
              key={workspace.id}
              onSelect={() => setActiveWorkspace(workspace.id)}
              className="justify-between"
            >
              <span className="flex items-center gap-2 truncate">
                <Check
                  className={cn(
                    "h-3.5 w-3.5",
                    workspace.id === activeWorkspace?.id ? "opacity-100" : "opacity-0",
                  )}
                />
                <span className="truncate">{workspace.name}</span>
              </span>
              <Badge variant="secondary" className="ml-2 capitalize">
                {workspace.role}
              </Badge>
            </DropdownMenuItem>
          ))}
          {!workspaces.length ? (
            <p className="px-2.5 py-2 text-sm text-muted-foreground">No workspaces yet.</p>
          ) : null}
        </DropdownMenuContent>
      </DropdownMenu>

      <div className="flex-1" />

      <NotificationBell />

      <Button
        variant="ghost"
        size="icon"
        onClick={toggleTheme}
        aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
      >
        {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
      </Button>

      {/* Account menu */}
      <DropdownMenu>
        <DropdownMenuTrigger className="flex items-center gap-2 rounded-full border p-0.5 pr-3 transition-colors hover:bg-accent">
          <Avatar>
            <AvatarFallback>{initialsOf(user?.full_name ?? user?.email)}</AvatarFallback>
          </Avatar>
          <span className="hidden max-w-[10rem] truncate text-sm font-medium sm:block">
            {user?.full_name ?? user?.email}
          </span>
        </DropdownMenuTrigger>
        <DropdownMenuContent className="w-60">
          <div className="px-2.5 py-2">
            <p className="truncate text-sm font-medium">{user?.full_name ?? "Signed in"}</p>
            <p className="truncate text-xs text-muted-foreground">{user?.email}</p>
            {user && !user.is_verified ? (
              <Badge variant="warning" className="mt-2">
                Email not verified
              </Badge>
            ) : null}
          </div>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => router.push("/settings")}>
            <Settings className="h-4 w-4" />
            Account settings
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => router.push("/ai-models")}>
            <Cpu className="h-4 w-4" />
            AI models
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem disabled={signingOut} onSelect={() => void handleSignOut(false)}>
            <LogOut className="h-4 w-4" />
            Sign out
          </DropdownMenuItem>
          <DropdownMenuItem
            disabled={signingOut}
            onSelect={() => void handleSignOut(true)}
            className="text-destructive focus-visible:text-destructive"
          >
            <ShieldAlert className="h-4 w-4" />
            Sign out of all devices
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </header>
  );
}
