import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server: http://localhost:5180. API calls go to the FastAPI backend on 127.0.0.1:8100 through the proxy,
// so the session + CSRF cookies are same-origin in development exactly as they are behind nginx in production.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5180,
    strictPort: true,
    proxy: {
      "/api": { target: "http://127.0.0.1:8100", changeOrigin: false },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    // Keep every asset as a separate file: the production CSP (default-src 'self') blocks data: URIs.
    assetsInlineLimit: 0,
  },
});
