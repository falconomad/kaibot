import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Vendor code changes rarely: separate chunks stay cached across deploys of the app code.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Tests read app.css as text (`?raw`) to check the focus and reduced-motion rules; by default vitest blanks CSS.
  test: { css: { include: /app\.css/ } },
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
