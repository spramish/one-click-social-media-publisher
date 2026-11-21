import { Rocket } from "lucide-react";

import { cn } from "@/lib/utils";

/** Product logo mark. `compact` hides the wordmark for tight spaces. */
export function Brand({
  className,
  compact = false,
}: {
  className?: string;
  compact?: boolean;
}) {
  return (
    <span className={cn("flex items-center gap-2.5", className)}>
      <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-primary-foreground shadow-sm">
        <Rocket className="h-4 w-4" />
      </span>
      {!compact ? (
        <span className="flex flex-col leading-none">
          <span className="text-sm font-semibold tracking-tight">One-Click Publisher</span>
          <span className="text-[10px] uppercase tracking-widest text-muted-foreground">
            Social video studio
          </span>
        </span>
      ) : null}
    </span>
  );
}
