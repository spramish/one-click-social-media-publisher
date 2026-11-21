import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

/**
 * Frontend test configuration (Phase 14).
 *
 * Vitest shares the project's `@/*` path alias and transforms TSX with the
 * automatic JSX runtime, so test files import the same modules the app does —
 * no Next.js build step is involved.
 */
export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./", import.meta.url)),
    },
  },
  esbuild: {
    jsx: "automatic",
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    include: ["**/*.test.{ts,tsx}"],
    exclude: ["node_modules/**", ".next/**"],
  },
});
