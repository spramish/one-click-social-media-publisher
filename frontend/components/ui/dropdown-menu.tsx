"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

type MenuContextValue = {
  open: boolean;
  setOpen: (open: boolean) => void;
};

const MenuContext = React.createContext<MenuContextValue | null>(null);

function useMenu(): MenuContextValue {
  const context = React.useContext(MenuContext);
  if (!context) throw new Error("DropdownMenu components must be used inside <DropdownMenu>");
  return context;
}

/**
 * Small dependency-free dropdown. Closes on outside click, Escape, and after a
 * selection. Renders a real button + a positioned panel so it stays keyboard
 * reachable without pulling in a full menu library.
 */
function DropdownMenu({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = React.useState(false);
  const containerRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <MenuContext.Provider value={{ open, setOpen }}>
      <div ref={containerRef} className={cn("relative", className)}>
        {children}
      </div>
    </MenuContext.Provider>
  );
}

function DropdownMenuTrigger({
  className,
  children,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const { open, setOpen } = useMenu();
  return (
    <button
      type="button"
      aria-haspopup="menu"
      aria-expanded={open}
      onClick={() => setOpen(!open)}
      className={className}
      {...props}
    >
      {children}
    </button>
  );
}

function DropdownMenuContent({
  align = "end",
  className,
  children,
}: {
  align?: "start" | "end";
  className?: string;
  children: React.ReactNode;
}) {
  const { open } = useMenu();
  if (!open) return null;
  return (
    <div
      role="menu"
      className={cn(
        "absolute z-50 mt-2 min-w-[12rem] overflow-hidden rounded-lg border bg-popover p-1 text-popover-foreground shadow-md",
        "animate-fade-in",
        align === "end" ? "right-0" : "left-0",
        className,
      )}
    >
      {children}
    </div>
  );
}

function DropdownMenuItem({
  className,
  onSelect,
  inset,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { onSelect?: () => void; inset?: boolean }) {
  const { setOpen } = useMenu();
  return (
    <button
      type="button"
      role="menuitem"
      onClick={() => {
        onSelect?.();
        setOpen(false);
      }}
      className={cn(
        "flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm transition-colors",
        "hover:bg-accent hover:text-accent-foreground focus-visible:bg-accent focus-visible:outline-none",
        "disabled:pointer-events-none disabled:opacity-50",
        inset && "pl-8",
        className,
      )}
      {...props}
    />
  );
}

function DropdownMenuSeparator({ className }: { className?: string }) {
  return <div role="separator" className={cn("-mx-1 my-1 h-px bg-border", className)} />;
}

function DropdownMenuLabel({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("px-2.5 py-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground", className)}
      {...props}
    />
  );
}

export {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuLabel,
};
