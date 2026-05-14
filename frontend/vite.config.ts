import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/auth": "http://localhost:8000",
      "/admin": "http://localhost:8000",
      "/webhook": "http://localhost:8000",
      "/billing": "http://localhost:8000",
      "/signup": "http://localhost:8000",
      "/health": "http://localhost:8000",
      "/metrics": "http://localhost:8000",
      // Proxy /portal/* only for API calls (XHR/fetch), not browser navigation
      "/portal": {
        target: "http://localhost:8000",
        bypass(req) {
          const accept = req.headers["accept"] ?? "";
          // If browser is navigating (accepts HTML), let Vite serve the SPA
          if (accept.includes("text/html")) return req.url;
          return null; // proxy to backend
        },
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
