import { Facebook, Instagram, Youtube } from "lucide-react";
import type { ComponentType } from "react";

import { cn } from "@/lib/utils";
import type { SocialPlatform } from "@/lib/platforms";

type IconProps = { className?: string };

function TikTokIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden className={className}>
      <path d="M16.5 3c.3 1.7 1.4 3 3 3.4V9c-1.2 0-2.3-.4-3.3-1v6.4a5.7 5.7 0 1 1-5.7-5.7c.3 0 .6 0 .9.1v2.7a3 3 0 1 0 2.1 2.9V3h3Z" />
    </svg>
  );
}

const ICONS: Record<SocialPlatform, ComponentType<IconProps>> = {
  instagram: Instagram,
  facebook: Facebook,
  youtube: Youtube,
  tiktok: TikTokIcon,
};

/** Small brand-coloured glyph for a platform key. */
export function PlatformIcon({
  platform,
  className,
}: {
  platform: SocialPlatform;
  className?: string;
}) {
  const Icon = ICONS[platform];
  const tone: Record<SocialPlatform, string> = {
    instagram: "text-pink-500",
    facebook: "text-blue-500",
    youtube: "text-red-500",
    tiktok: "text-foreground",
  };
  return <Icon className={cn("h-4 w-4", tone[platform], className)} />;
}
