"use client";

import { AlertCircle, CheckCircle2, Info, TriangleAlert, X } from "lucide-react";

import { useToastQueue, type ToastVariant } from "@/hooks/use-toast";
import { cn } from "@/lib/utils";

const VARIANT_STYLES: Record<ToastVariant, string> = {
  default: "border-border bg-popover text-popover-foreground [&_svg.icon]:text-foreground",
  success: "border-success/40 bg-popover [&_svg.icon]:text-success",
  destructive: "border-destructive/50 bg-popover [&_svg.icon]:text-destructive",
  warning: "border-warning/40 bg-popover [&_svg.icon]:text-warning",
  info: "border-info/40 bg-popover [&_svg.icon]:text-info",
};

const VARIANT_ICONS: Record<ToastVariant, React.ComponentType<{ className?: string }>> = {
  default: Info,
  success: CheckCircle2,
  destructive: AlertCircle,
  warning: TriangleAlert,
  info: Info,
};

/** Renders the active toast queue in the bottom-right corner. */
export function Toaster() {
  const { toasts, dismiss } = useToastQueue();

  return (
    <div
      aria-live="polite"
      aria-atomic="true"
      className="pointer-events-none fixed bottom-4 right-4 z-[100] flex w-full max-w-sm flex-col gap-2"
    >
      {toasts.map((item) => {
        const Icon = VARIANT_ICONS[item.variant];
        return (
          <div
            key={item.id}
            role="status"
            className={cn(
              "pointer-events-auto flex items-start gap-3 rounded-lg border p-4 shadow-lg animate-slide-in-right",
              VARIANT_STYLES[item.variant],
            )}
          >
            <Icon className="icon mt-0.5 h-4 w-4 shrink-0" />
            <div className="flex-1 space-y-1">
              <p className="text-sm font-medium leading-none">{item.title}</p>
              {item.description ? (
                <p className="text-sm text-muted-foreground">{item.description}</p>
              ) : null}
            </div>
            <button
              type="button"
              aria-label="Dismiss notification"
              onClick={() => dismiss(item.id)}
              className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
