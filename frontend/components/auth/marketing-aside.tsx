import { Badge } from "@/components/ui/badge";
import { PlatformIcon } from "@/components/platform-icon";
import { PLATFORMS } from "@/lib/platforms";

/** Product story shown beside the sign-in / sign-up forms on wide screens. */
export function MarketingAside() {
  return (
    <div className="space-y-8">
      <div className="space-y-3">
        <Badge variant="secondary">Publish everywhere in one click</Badge>
        <h2 className="text-2xl font-semibold tracking-tight">
          One upload. Every platform. One action.
        </h2>
        <p className="text-sm text-muted-foreground">
          Upload a video once, tailor the caption per network, and publish to all of them
          together — each platform handled by its own official API integration.
        </p>
      </div>

      <ul className="space-y-3">
        {PLATFORMS.map((platform) => (
          <li
            key={platform.key}
            className="flex items-center gap-3 rounded-lg border bg-card px-4 py-3"
          >
            <PlatformIcon platform={platform.key} className="h-5 w-5" />
            <div>
              <p className="text-sm font-medium">{platform.name}</p>
              <p className="text-xs text-muted-foreground">{platform.api}</p>
            </div>
          </li>
        ))}
      </ul>

      <p className="text-xs text-muted-foreground">
        Per-platform captions, scheduling with time zones, publishing history, and analytics
        land in the following phases.
      </p>
    </div>
  );
}
