/** The four platforms the product targets, in publishing order. */
export type SocialPlatform = "instagram" | "facebook" | "youtube" | "tiktok";

export type PlatformMeta = {
  key: SocialPlatform;
  name: string;
  /** The official API this platform is integrated through. */
  api: string;
};

export const PLATFORMS: PlatformMeta[] = [
  { key: "instagram", name: "Instagram", api: "Instagram Graph API" },
  { key: "facebook", name: "Facebook", api: "Meta Graph API" },
  { key: "youtube", name: "YouTube", api: "YouTube Data API v3" },
  { key: "tiktok", name: "TikTok", api: "TikTok Content Posting API" },
];
