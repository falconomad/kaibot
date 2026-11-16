import { defineConfig } from "vitest/config";
import type { Plugin } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { MANIFEST_PATH, brandHtml, manifestJson } from "./src/brand/assets";

/** The display name in the two files that are not React (#254): the title and meta tags of index.html (%BRAND%) and the
 *  web app manifest. Both are written from src/brand/brand.ts, so the name exists in one place. */
function brand(): Plugin {
  return {
    name: "kb-brand",
    transformIndexHtml: { order: "pre", handler: (html) => brandHtml(html) },
    configureServer(server) {
      server.middlewares.use(`/${MANIFEST_PATH}`, (_req, res) => {
        res.setHeader("Content-Type", "application/manifest+json");
        res.end(manifestJson());
      });
    },
    generateBundle() {
      this.emitFile({ type: "asset", fileName: MANIFEST_PATH, source: manifestJson() });
    },
  };
}

// Vendor code changes rarely: separate chunks stay cached across deploys of the app code.
export default defineConfig({
  plugins: [react(), tailwindcss(), brand()],
  // Tests read app.css as text (`?raw`) to check the focus and reduced-motion rules; by default vitest blanks CSS.
  test: { css: { include: /(app|v4tokens)\.css/ } },
  build: {
    rollupOptions: {
      output: {
        // By package folder, not by import name: `react-dom/client` and `scheduler` are separate entry files, and
        // listing only "react-dom" left 190 kB of React inside the app chunk, re-downloaded on every deploy.
        manualChunks(id) {
          const m = /[\\/]node_modules[\\/]((?:@[^\\/]+[\\/])?[^\\/]+)/.exec(id);
          if (!m) return undefined;
          const pkg = m[1].replace("\\", "/");
          if (["react", "react-dom", "scheduler", "react-router", "react-router-dom"].includes(pkg)) return "react";
          if (pkg.startsWith("@supabase/")) return "supabase";
          if (pkg.startsWith("@tanstack/")) return "query";
          return undefined;
        },
      },
    },
  },
});
