"use client";

import Link from "next/link";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth-context";

/** Auth-aware call-to-action buttons for the marketing landing page. */
export function LandingActions({ size = "default" }: { size?: "default" | "lg" }) {
  const { status } = useAuth();

  if (status === "authenticated") {
    return (
      <Button asChild size={size}>
        <Link href="/dashboard">Open dashboard</Link>
      </Button>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-3">
      <Button asChild size={size}>
        <Link href="/register">Create free account</Link>
      </Button>
      <Button asChild variant="outline" size={size}>
        <Link href="/login">Sign in</Link>
      </Button>
    </div>
  );
}

/** Compact header links for the marketing nav. */
export function LandingNavActions() {
  const { status } = useAuth();

  if (status === "authenticated") {
    return (
      <Button asChild size="sm">
        <Link href="/dashboard">Dashboard</Link>
      </Button>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <Button asChild variant="ghost" size="sm">
        <Link href="/login">Sign in</Link>
      </Button>
      <Button asChild size="sm">
        <Link href="/register">Get started</Link>
      </Button>
    </div>
  );
}
