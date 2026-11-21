import { Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";

/** Inline loading spinner. */
export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("h-4 w-4 animate-spin", className)} aria-hidden />;
}

/** Full-panel loading state with an accessible label. */
export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-3 py-16 text-sm text-muted-foreground">
      <Spinner />
      <span>{label}</span>
    </div>
  );
}
