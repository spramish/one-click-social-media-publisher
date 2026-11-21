"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  ArrowRight,
  BarChart3,
  CheckCircle2,
  Circle,
  Share2,
  Sparkles,
  UploadCloud,
  Users,
} from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Avatar, AvatarFallback, initialsOf } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { formatDate } from "@/lib/format";
import type { WorkspaceMember } from "@/lib/types";

const QUICK_ACTIONS = [
  { href: "/upload", label: "Upload a video", icon: UploadCloud, phase: 5 },
  { href: "/compose", label: "Compose a post", icon: Sparkles, phase: 7 },
  { href: "/accounts", label: "Connect accounts", icon: Share2, phase: 6 },
  { href: "/analytics", label: "View analytics", icon: BarChart3, phase: 10 },
];

export default function DashboardPage() {
  const { user, activeWorkspace } = useAuth();
  const [members, setMembers] = useState<WorkspaceMember[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!activeWorkspace) return;
    let cancelled = false;
    setMembers(null);
    setError(null);
    (async () => {
      try {
        const list = await api.workspaces.members(activeWorkspace.id);
        if (!cancelled) setMembers(list);
      } catch {
        if (!cancelled) setError("Could not load this workspace's members.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeWorkspace]);

  const checklist = [
    {
      label: "Verify your email address",
      done: Boolean(user?.is_verified),
      note: user?.is_verified ? "Verified" : "Check your inbox for the verification link",
    },
    { label: "Connect a social account", done: false, note: "Link a platform and approve access", href: "/accounts" },
    { label: "Upload your first video", done: false, note: "Add a video to your library", href: "/upload" },
    { label: "Publish to every platform", done: false, note: "Phase 7", href: "/compose" },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title={`Welcome back${user?.full_name ? `, ${user.full_name.split(" ")[0]}` : ""}`}
        description={
          activeWorkspace
            ? `You are working in ${activeWorkspace.name} as ${activeWorkspace.role}.`
            : "Set up your first workspace to get started."
        }
        action={
          <Button asChild>
            <Link href="/upload">
              <UploadCloud className="h-4 w-4" />
              Upload video
            </Link>
          </Button>
        }
      />

      <div className="grid gap-5 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Workspace</CardTitle>
            <CardDescription>The tenant this session is scoped to.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            {activeWorkspace ? (
              <>
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">Name</span>
                  <span className="font-medium">{activeWorkspace.name}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">Role</span>
                  <Badge variant="secondary" className="capitalize">
                    {activeWorkspace.role}
                  </Badge>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">Slug</span>
                  <code className="font-mono text-xs">{activeWorkspace.slug}</code>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">Created</span>
                  <span>{formatDate(activeWorkspace.created_at)}</span>
                </div>
              </>
            ) : (
              <p className="text-muted-foreground">No workspace selected.</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Team</CardTitle>
            <CardDescription>Members with access to this workspace.</CardDescription>
          </CardHeader>
          <CardContent className="text-sm">
            {members === null && !error ? (
              <div className="space-y-2">
                <Skeleton className="h-5 w-24" />
                <Skeleton className="h-4 w-40" />
              </div>
            ) : error ? (
              <p className="text-destructive">{error}</p>
            ) : (
              <>
                <p className="text-3xl font-semibold">{members?.length ?? 0}</p>
                <p className="mt-1 inline-flex items-center gap-1.5 text-muted-foreground">
                  <Users className="h-4 w-4" />
                  {members?.length === 1 ? "member" : "members"}
                </p>
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Getting started</CardTitle>
            <CardDescription>Finish setup to unlock publishing.</CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2.5 text-sm">
              {checklist.map((item) => (
                <li key={item.label} className="flex items-start gap-2">
                  {item.done ? (
                    <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" />
                  ) : (
                    <Circle className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground/50" />
                  )}
                  <span className="flex-1">
                    <span className={item.done ? "text-muted-foreground line-through" : ""}>
                      {item.label}
                    </span>
                    <span className="block text-xs text-muted-foreground">{item.note}</span>
                  </span>
                  {item.href ? (
                    <Link
                      href={item.href}
                      className="shrink-0 text-xs font-medium text-primary hover:underline"
                    >
                      Open
                    </Link>
                  ) : null}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Quick actions</CardTitle>
          <CardDescription>
            These modules are delivered in later phases of the build.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {QUICK_ACTIONS.map((action) => (
            <Link
              key={action.href}
              href={action.href}
              className="group flex items-center justify-between rounded-lg border bg-card px-4 py-3 transition-colors hover:bg-accent"
            >
              <span className="flex items-center gap-3">
                <span className="flex h-9 w-9 items-center justify-center rounded-md bg-primary/10 text-primary">
                  <action.icon className="h-4 w-4" />
                </span>
                <span className="text-sm font-medium">{action.label}</span>
              </span>
              <ArrowRight className="h-4 w-4 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
            </Link>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Workspace members</CardTitle>
          <CardDescription>
            Read through the Phase 3 API — role-based access applies to every endpoint.
          </CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {members === null && !error ? (
            <div className="space-y-3 p-6">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : error ? (
            <div className="p-6">
              <Alert variant="destructive">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            </div>
          ) : members && members.length ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Member</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Joined</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {members.map((member) => (
                  <TableRow key={member.id}>
                    <TableCell>
                      <span className="flex items-center gap-3">
                        <Avatar>
                          <AvatarFallback>
                            {initialsOf(member.full_name ?? member.email)}
                          </AvatarFallback>
                        </Avatar>
                        <span className="flex flex-col">
                          <span className="font-medium">{member.full_name ?? "—"}</span>
                          <span className="text-xs text-muted-foreground">{member.email}</span>
                        </span>
                      </span>
                    </TableCell>
                    <TableCell className="capitalize">{member.role}</TableCell>
                    <TableCell>
                      <Badge variant={member.status === "active" ? "success" : "secondary"}>
                        {member.status}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {formatDate(member.joined_at)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <div className="p-6">
              <EmptyState
                icon={Users}
                title="No members yet"
                description="Invite teammates once member management lands in a later phase."
              />
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
