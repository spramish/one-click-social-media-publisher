import type { Metadata } from "next";

import "./globals.css";
import { Toaster } from "@/components/ui/toaster";
import { AuthProvider } from "@/lib/auth-context";
import { ThemeProvider } from "@/lib/theme-context";

export const metadata: Metadata = {
  title: {
    default: "One-Click Social Media Video Publisher",
    template: "%s · One-Click Publisher",
  },
  description:
    "Upload one video and publish it to Instagram, Facebook, YouTube, and TikTok from a single dashboard.",
};

/**
 * Applies the stored theme before first paint so a dark-mode user never sees a
 * flash of the light theme. Kept inline and tiny on purpose.
 */
const THEME_SCRIPT = `(function(){try{var t=localStorage.getItem("ocp.theme")||"dark";var r=document.documentElement;if(t==="dark"){r.classList.add("dark")}else{r.classList.remove("dark")}r.style.colorScheme=t}catch(e){}})();`;

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body>
        <ThemeProvider>
          <AuthProvider>
            {children}
            <Toaster />
          </AuthProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
