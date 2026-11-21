"use client";

import * as React from "react";

export type ToastVariant = "default" | "success" | "destructive" | "warning" | "info";

export type ToastItem = {
  id: string;
  title: string;
  description?: string;
  variant: ToastVariant;
  duration: number;
};

type ToastInput = {
  title: string;
  description?: string;
  variant?: ToastVariant;
  duration?: number;
};

/**
 * Module-level toast store (mirrors the shadcn/ui pattern) so a toast can be
 * raised from anywhere — including non-React code such as the API client —
 * without threading a context through the tree.
 */
let toasts: ToastItem[] = [];
const listeners = new Set<(items: ToastItem[]) => void>();

function emit() {
  for (const listener of listeners) listener(toasts);
}

export function toast(input: ToastInput): string {
  const id = `toast-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const item: ToastItem = {
    id,
    title: input.title,
    description: input.description,
    variant: input.variant ?? "default",
    duration: input.duration ?? 5000,
  };
  toasts = [...toasts, item];
  emit();
  window.setTimeout(() => dismissToast(id), item.duration);
  return id;
}

export function dismissToast(id: string) {
  toasts = toasts.filter((item) => item.id !== id);
  emit();
}

/** Subscribe to the toast queue. Used by `<Toaster />`. */
export function useToastQueue() {
  const [items, setItems] = React.useState<ToastItem[]>(toasts);

  React.useEffect(() => {
    listeners.add(setItems);
    setItems(toasts);
    return () => {
      listeners.delete(setItems);
    };
  }, []);

  return { toasts: items, dismiss: dismissToast };
}
