import type { Metadata } from "next";
import Link from "next/link";
import {
  BarChart3,
  CalendarClock,
  Layers,
  PenSquare,
  ShieldCheck,
  Sparkles,
  UploadCloud,
} from "lucide-react";

import { Brand } from "@/components/brand";
import { LandingActions, LandingNavActions } from "@/components/landing-actions";
import { PlatformIcon } from "@/components/platform-icon";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PLATFORMS } from "@/lib/platforms";

export const metadata: Metadata = {
  title: "One-Click Social Media Video Publisher",
  description:
    "Upload a video once and publish it to Instagram, Facebook, YouTube, and TikTok from a single dashboard.",
};

const FEATURES = [
  {
    icon: UploadCloud,
    title: "Upload once",
    description:
      "Bring a single video file into a managed library with metadata, thumbnails, and per-platform variants.",
  },
  {
    icon: PenSquare,
    title: "Compose per platform",
    description:
      "Caption, hashtags, and title are tailored for each destination instead of copy-pasted blindly.",
  },
  {
    icon: Layers,
    title: "Multi-account publishing",
    description:
      "Connect several accounts per platform and choose exactly which destinations receive each post.",
  },
  {
    icon: CalendarClock,
    title: "Scheduling calendar",
    description:
      "Queue posts for a future time and review the whole week before anything goes live.",
  },
  {
    icon: BarChart3,
    title: "Publishing history",
    description:
      "Every attempt is logged with its status and platform response, so failures are visible, not silent.",
  },
  {
    icon: Sparkles,
    title: "AI content assistant",
    description:
      "Turn a short brief into captions and hashtags that fit each platform's audience.",
  },
];

export default function HomePage() {
  return (
    <div className="min-h-screen bg-background">
      <header className="sticky top-0 z-40 border-b bg-background/80 backdrop-blur">
        <div className="mx-auto flex h-16 w-full max-w-6xl items-center justify-between px-4 lg:px-8">
          <Link href="/" aria-label="One-Click Publisher home">
            <Brand />
          </Link>
          <LandingNavActions />
        </div>
      </header>

      <main className="mx-auto w-full max-w-6xl px-4 lg:px-8">
        <section className="py-16 sm:py-24">
          <div className="max-w-3xl space-y-6">
            <Badge variant="secondary">One studio for every platform</Badge>
            <h1 className="text-4xl font-bold tracking-tight sm:text-5xl">
              Publish one video to Instagram, Facebook, YouTube, and TikTok — in one click.
            </h1>
            <p className="text-lg text-muted-foreground">
              One-Click Social Media Video Publisher keeps your video library, captions,
              schedules, and publishing history in a single workspace, with role-based access
              for the whole team.
            </p>
            <LandingActions size="lg" />
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2 pt-2 text-sm text-muted-foreground">
              <span className="inline-flex items-center gap-2">
                <ShieldCheck className="h-4 w-4" />
                Secure, tenant-isolated workspaces
              </span>
              <span className="inline-flex items-center gap-2">
                {PLATFORMS.map((platform) => (
                  <PlatformIcon key={platform.key} platform={platform.key} />
                ))}
                Four native platform integrations
              </span>
            </div>
          </div>
        </section>

        <section aria-labelledby="features-heading" className="pb-16">
          <div className="mb-8 space-y-2">
            <h2 id="features-heading" className="text-2xl font-semibold tracking-tight">
              Built for teams that publish daily
            </h2>
            <p className="text-sm text-muted-foreground">
              The dashboard below is delivered in this phase; each publishing integration is
              added platform by platform on top of it.
            </p>
          </div>
          <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {FEATURES.map((feature) => (
              <Card key={feature.title}>
                <CardHeader>
                  <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
                    <feature.icon className="h-5 w-5" />
                  </span>
                  <CardTitle className="pt-2">{feature.title}</CardTitle>
                  <CardDescription>{feature.description}</CardDescription>
                </CardHeader>
              </Card>
            ))}
          </div>
        </section>

        <section aria-labelledby="platforms-heading" className="pb-20">
          <Card>
            <CardHeader>
              <CardTitle id="platforms-heading">Platform coverage</CardTitle>
              <CardDescription>
                Each destination is integrated through its own official API — one publishing
                request is never identical across platforms.
              </CardDescription>
            </CardHeader>
            <CardContent className="grid gap-3 sm:grid-cols-2">
              {PLATFORMS.map((platform) => (
                <div
                  key={platform.key}
                  className="flex items-center gap-3 rounded-md border bg-secondary/30 px-4 py-3"
                >
                  <PlatformIcon platform={platform.key} className="h-5 w-5" />
                  <div>
                    <p className="text-sm font-medium">{platform.name}</p>
                    <p className="text-xs text-muted-foreground">{platform.api}</p>
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>
        </section>
      </main>

      <footer className="border-t">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-3 px-4 py-8 text-sm text-muted-foreground sm:flex-row sm:items-center sm:justify-between lg:px-8">
          <p>© {new Date().getFullYear()} One-Click Social Media Video Publisher</p>
          <div className="flex items-center gap-4">
            <Link href="/login" className="hover:text-foreground">
              Sign in
            </Link>
            <Link href="/register" className="hover:text-foreground">
              Create account
            </Link>
          </div>
        </div>
      </footer>
    </div>
  );
}
